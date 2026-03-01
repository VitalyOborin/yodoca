"""SQLite journal storage for the Event Bus."""

import json
import logging
import time
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_EventRow = tuple[int, str, str, dict, float, str | None, int]


def _row_to_event_tuple(row: tuple) -> _EventRow:
    """Convert journal row to (id, topic, source, payload, created_at, correlation_id, retry_count)."""
    payload = json.loads(row[3]) if isinstance(row[3], str) else row[3]
    retry_count = row[6] if len(row) > 6 else 0
    return (row[0], row[1], row[2], payload, row[4], row[5], retry_count)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id  TEXT,
    topic           TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    payload         TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      REAL    NOT NULL,
    processed_at    REAL,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_ej_topic_status ON event_journal(topic, status);
CREATE INDEX IF NOT EXISTS idx_ej_status_created ON event_journal(status, created_at);
CREATE INDEX IF NOT EXISTS idx_ej_correlation ON event_journal(correlation_id);
"""


class EventJournal:
    """SQLite-backed event journal. One connection per instance."""

    def __init__(self, db_path: Path, busy_timeout: int = 5000) -> None:
        self._db_path = db_path
        self._busy_timeout = busy_timeout
        self._conn: aiosqlite.Connection | None = None

    async def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(self._db_path))
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.execute(f"PRAGMA busy_timeout={self._busy_timeout}")
            await self._conn.executescript(_SCHEMA)
            await self._conn.commit()
            await self._migrate_retry_count()
            await self._migrate_processing_since()
        return self._conn

    async def _get_table_columns(self) -> set[str]:
        """Return set of column names for event_journal. Requires _conn set."""
        if self._conn is None:
            return set()
        cursor = await self._conn.execute("PRAGMA table_info(event_journal)")
        rows = await cursor.fetchall()
        return {row[1] for row in rows}

    async def _migrate_retry_count(self) -> None:
        """Add retry_count column if missing (migration for existing DBs)."""
        if self._conn is None:
            return
        if "retry_count" not in await self._get_table_columns():
            await self._conn.execute(
                "ALTER TABLE event_journal ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
            )
            await self._conn.commit()

    async def _migrate_processing_since(self) -> None:
        """Add processing_since column if missing (migration for existing DBs)."""
        if self._conn is None:
            return
        if "processing_since" not in await self._get_table_columns():
            await self._conn.execute(
                "ALTER TABLE event_journal ADD COLUMN processing_since REAL"
            )
            await self._conn.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def insert(
        self,
        topic: str,
        source: str,
        payload: dict,
        correlation_id: str | None = None,
    ) -> int:
        """Insert event and return row id."""
        conn = await self._ensure_conn()
        now = time.time()
        cursor = await conn.execute(
            """
            INSERT INTO event_journal (topic, source, payload, correlation_id, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (
                topic,
                source,
                json.dumps(payload, ensure_ascii=False),
                correlation_id,
                now,
            ),
        )
        await conn.commit()
        return cursor.lastrowid or 0

    async def fetch_pending(self, limit: int = 3) -> list[_EventRow]:
        """Fetch pending events by created_at. Returns list of (id, topic, source, payload, created_at, correlation_id, retry_count).
        Deprecated: use claim_pending for atomic claim."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            """
            SELECT id, topic, source, payload, created_at, correlation_id, retry_count
            FROM event_journal
            WHERE status = 'pending'
            ORDER BY created_at
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [_row_to_event_tuple(row) for row in rows]

    async def claim_pending(self, limit: int = 3) -> list[_EventRow]:
        """Atomically claim pending events: SELECT + UPDATE status='processing' in one transaction.
        Returns list of (id, topic, source, payload, created_at, correlation_id, retry_count)."""
        conn = await self._ensure_conn()
        now = time.time()
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                """
                SELECT id, topic, source, payload, created_at, correlation_id, retry_count
                FROM event_journal
                WHERE status = 'pending'
                ORDER BY created_at
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            ids = [row[0] for row in rows]
            if ids:
                placeholders = ",".join("?" * len(ids))
                await conn.execute(
                    f"UPDATE event_journal SET status = 'processing', processing_since = ? "
                    f"WHERE id IN ({placeholders})",
                    [now, *ids],
                )
            await conn.commit()
            return [_row_to_event_tuple(row) for row in rows]
        except Exception:
            await conn.rollback()
            raise

    async def mark_processing(self, event_id: int) -> None:
        """Mark event as processing."""
        conn = await self._ensure_conn()
        await conn.execute(
            "UPDATE event_journal SET status = 'processing' WHERE id = ?",
            (event_id,),
        )
        await conn.commit()

    async def mark_done(self, event_id: int) -> None:
        """Mark event as done."""
        conn = await self._ensure_conn()
        now = time.time()
        await conn.execute(
            "UPDATE event_journal SET status = 'done', processed_at = ? WHERE id = ?",
            (now, event_id),
        )
        await conn.commit()

    async def mark_failed(self, event_id: int, error: str) -> None:
        """Mark event as failed with error message."""
        conn = await self._ensure_conn()
        now = time.time()
        await conn.execute(
            "UPDATE event_journal SET status = 'failed', processed_at = ?, error = ? WHERE id = ?",
            (now, error, event_id),
        )
        await conn.commit()

    async def mark_retry(self, event_id: int) -> None:
        """Set status to pending and increment retry_count for at-least-once retry."""
        conn = await self._ensure_conn()
        await conn.execute(
            """
            UPDATE event_journal
            SET status = 'pending', retry_count = retry_count + 1
            WHERE id = ?
            """,
            (event_id,),
        )
        await conn.commit()

    async def mark_dead_letter(self, event_id: int, error: str) -> None:
        """Mark event as dead_letter after max retries exceeded."""
        conn = await self._ensure_conn()
        now = time.time()
        await conn.execute(
            "UPDATE event_journal SET status = 'dead_letter', processed_at = ?, error = ? WHERE id = ?",
            (now, error, event_id),
        )
        await conn.commit()

    async def reset_processing_to_pending(self) -> int:
        """Reset all 'processing' events to 'pending'. Return count. Used at startup recovery."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "UPDATE event_journal SET status = 'pending', processing_since = NULL "
            "WHERE status = 'processing'"
        )
        await conn.commit()
        return cursor.rowcount or 0

    async def _reset_stale_to_pending(
        self, conn: aiosqlite.Connection, now: float, stale_threshold: float, max_retries: int
    ) -> int:
        """Reset events stuck in processing (retry_count < max) to pending. Return count."""
        cursor = await conn.execute(
            """
            UPDATE event_journal
            SET status = 'pending', retry_count = retry_count + 1, processing_since = NULL
            WHERE status = 'processing' AND processing_since < ? AND retry_count < ?
            """,
            (now - stale_threshold, max_retries),
        )
        return cursor.rowcount or 0

    async def _dead_letter_stale(
        self, conn: aiosqlite.Connection, now: float, stale_threshold: float, max_retries: int
    ) -> int:
        """Dead-letter events stuck in processing (retry_count >= max). Return count."""
        cursor = await conn.execute(
            """
            UPDATE event_journal
            SET status = 'dead_letter', processed_at = ?, error = 'stale: max retries exceeded',
                processing_since = NULL
            WHERE status = 'processing' AND processing_since < ? AND retry_count >= ?
            """,
            (now, now - stale_threshold, max_retries),
        )
        return cursor.rowcount or 0

    async def recover_stale(
        self, stale_threshold: float, max_retries: int
    ) -> tuple[int, int]:
        """Reset events stuck in 'processing' longer than threshold.
        Events with retry_count < max_retries go to 'pending' (retry_count incremented).
        Events with retry_count >= max_retries go to 'dead_letter'.
        Returns (reset_count, dead_letter_count)."""
        conn = await self._ensure_conn()
        now = time.time()
        reset_count = await self._reset_stale_to_pending(
            conn, now, stale_threshold, max_retries
        )
        dead_letter_count = await self._dead_letter_stale(
            conn, now, stale_threshold, max_retries
        )
        await conn.commit()
        return (reset_count, dead_letter_count)
