"""SQLite journal storage for the Event Bus."""

import json
import logging
import time
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id  TEXT,
    topic           TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    payload         TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
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

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(self._db_path))
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.executescript(_SCHEMA)
            await self._conn.commit()
        return self._conn

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
            (topic, source, json.dumps(payload), correlation_id, now),
        )
        await conn.commit()
        return cursor.lastrowid or 0

    async def count_pending(self, exclude_topic: str | None = None) -> int:
        """Count pending events. Optionally exclude a topic (e.g. system.agent.background)."""
        conn = await self._ensure_conn()
        if exclude_topic:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM event_journal WHERE status = 'pending' AND topic != ?",
                (exclude_topic,),
            )
        else:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM event_journal WHERE status = 'pending'"
            )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def fetch_pending(self, limit: int = 3) -> list[tuple[int, str, str, dict, float, str | None]]:
        """Fetch pending events by created_at. Returns list of (id, topic, source, payload, created_at, correlation_id)."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            """
            SELECT id, topic, source, payload, created_at, correlation_id
            FROM event_journal
            WHERE status = 'pending'
            ORDER BY created_at
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        result: list[tuple[int, str, str, dict, float, str | None]] = []
        for row in rows:
            payload = json.loads(row[3]) if isinstance(row[3], str) else row[3]
            result.append((row[0], row[1], row[2], payload, row[4], row[5]))
        return result

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

    async def reset_processing_to_pending(self) -> int:
        """Reset all 'processing' events to 'pending'. Return count."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "UPDATE event_journal SET status = 'pending' WHERE status = 'processing'"
        )
        await conn.commit()
        return cursor.rowcount or 0
