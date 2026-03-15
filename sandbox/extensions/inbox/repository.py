"""SQLite-backed Inbox repository: items and cursors."""

import hashlib
import json
import time
from pathlib import Path

import aiosqlite

from sandbox.extensions.inbox.models import InboxItemInput

_INBOX_ITEMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT    NOT NULL,
    source_account  TEXT    NOT NULL,
    entity_type     TEXT    NOT NULL,
    external_id     TEXT    NOT NULL,
    title           TEXT    NOT NULL DEFAULT '',
    occurred_at     REAL    NOT NULL,
    ingested_at     REAL    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active',
    is_read         INTEGER NOT NULL DEFAULT 0,
    is_current      INTEGER NOT NULL DEFAULT 1,
    payload         TEXT    NOT NULL DEFAULT '{}',
    payload_hash    TEXT    NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_inbox_current
  ON inbox_items(source_type, source_account, entity_type, external_id)
  WHERE is_current = 1;

CREATE INDEX IF NOT EXISTS idx_inbox_source
  ON inbox_items(source_type, source_account, is_current);

CREATE INDEX IF NOT EXISTS idx_inbox_ingested
  ON inbox_items(ingested_at);
"""

_INBOX_UNREAD_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_inbox_unread
  ON inbox_items(is_read, is_current, status);
"""

_INBOX_CURSORS_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_cursors (
    source_type    TEXT NOT NULL,
    source_account TEXT NOT NULL,
    stream         TEXT NOT NULL,
    cursor_value   TEXT NOT NULL,
    updated_at     REAL NOT NULL,
    PRIMARY KEY (source_type, source_account, stream)
);
"""


def _compute_payload_hash(payload: dict) -> str:
    """SHA-256 hex digest of canonical JSON payload."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_to_dict(row: aiosqlite.Row) -> dict:
    """Convert aiosqlite Row to dict for InboxItem."""
    return {
        "id": row["id"],
        "source_type": row["source_type"],
        "source_account": row["source_account"],
        "entity_type": row["entity_type"],
        "external_id": row["external_id"],
        "title": row["title"] or "",
        "occurred_at": row["occurred_at"],
        "ingested_at": row["ingested_at"],
        "status": row["status"] or "active",
        "is_read": bool(row["is_read"]),
        "is_current": bool(row["is_current"]),
        "payload": json.loads(row["payload"] or "{}"),
        "payload_hash": row["payload_hash"] or "",
    }


class InboxRepository:
    """SQLite-backed store for inbox items and cursors."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def ensure_conn(self) -> aiosqlite.Connection:
        """Open connection and ensure schema. Idempotent."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(self._db_path))
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.executescript(_INBOX_ITEMS_SCHEMA)
            await self._ensure_items_schema_migrations(self._conn)
            await self._conn.executescript(_INBOX_UNREAD_INDEX_SCHEMA)
            await self._conn.executescript(_INBOX_CURSORS_SCHEMA)
            await self._conn.commit()
        return self._conn

    async def _ensure_items_schema_migrations(self, conn: aiosqlite.Connection) -> None:
        """Apply forward-compatible inbox_items schema migrations."""
        cursor = await conn.execute("PRAGMA table_info(inbox_items)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "is_read" not in cols:
            await conn.execute(
                "ALTER TABLE inbox_items ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0"
            )

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def upsert_item(self, input: InboxItemInput) -> tuple[int, str, float]:
        """Upsert item. Returns (inbox_id, change_type, ingested_at).
        ingested_at=0 for duplicate."""
        conn = await self.ensure_conn()
        conn.row_factory = aiosqlite.Row
        payload_json = json.dumps(input.payload, ensure_ascii=False)
        payload_hash = _compute_payload_hash(input.payload)
        ingested_at = time.time()

        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                """
                SELECT id, payload_hash, status, ingested_at, is_read FROM inbox_items
                WHERE source_type = ? AND source_account = ? AND entity_type = ?
                  AND external_id = ? AND is_current = 1
                """,
                (
                    input.source_type,
                    input.source_account,
                    input.entity_type,
                    input.external_id,
                ),
            )
            row = await cursor.fetchone()

            if row is None:
                await conn.execute(
                    """
                    INSERT INTO inbox_items (
                        source_type, source_account, entity_type, external_id,
                        title, occurred_at, ingested_at, status, is_read, is_current,
                        payload, payload_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                    """,
                    (
                        input.source_type,
                        input.source_account,
                        input.entity_type,
                        input.external_id,
                        input.title,
                        input.occurred_at,
                        ingested_at,
                        input.status,
                        payload_json,
                        payload_hash,
                    ),
                )
                await conn.commit()
                cursor = await conn.execute("SELECT last_insert_rowid()")
                new_id = (await cursor.fetchone())[0]
                change_type = "deleted" if input.status == "deleted" else "created"
                return (new_id, change_type, ingested_at)

            existing_id = row["id"]
            existing_hash = row["payload_hash"] or ""
            existing_status = row["status"] or "active"
            existing_is_read = int(row["is_read"] or 0)

            if input.status == "deleted":
                if existing_status == "deleted":
                    await conn.rollback()
                    return (existing_id, "duplicate", 0.0)
                await conn.execute(
                    "UPDATE inbox_items SET status = 'deleted', ingested_at = ? "
                    "WHERE id = ?",
                    (ingested_at, existing_id),
                )
                await conn.commit()
                return (existing_id, "deleted", ingested_at)

            if existing_hash == payload_hash:
                await conn.rollback()
                return (existing_id, "duplicate", 0.0)

            await conn.execute(
                "UPDATE inbox_items SET is_current = 0 WHERE id = ?",
                (existing_id,),
            )
            await conn.execute(
                """
                INSERT INTO inbox_items (
                    source_type, source_account, entity_type, external_id,
                    title, occurred_at, ingested_at, status, is_read, is_current,
                    payload, payload_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    input.source_type,
                    input.source_account,
                    input.entity_type,
                    input.external_id,
                    input.title,
                    input.occurred_at,
                    ingested_at,
                    input.status,
                    existing_is_read,
                    payload_json,
                    payload_hash,
                ),
            )
            await conn.commit()
            cursor = await conn.execute("SELECT last_insert_rowid()")
            new_id = (await cursor.fetchone())[0]
            return (new_id, "updated", ingested_at)
        except Exception:
            await conn.rollback()
            raise

    async def get_item(self, inbox_id: int) -> dict | None:
        """Fetch single item by id."""
        conn = await self.ensure_conn()
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM inbox_items WHERE id = ?", (inbox_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    async def list_items(
        self,
        *,
        source_type: str | None = None,
        entity_type: str | None = None,
        status: str = "active",
        is_read: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """List items with filters. Returns (rows, total_count)."""
        conn = await self.ensure_conn()
        conn.row_factory = aiosqlite.Row

        conditions = ["is_current = 1"]
        params: list[object] = []
        if source_type is not None:
            conditions.append("source_type = ?")
            params.append(source_type)
        if entity_type is not None:
            conditions.append("entity_type = ?")
            params.append(entity_type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if is_read is not None:
            conditions.append("is_read = ?")
            params.append(1 if is_read else 0)

        where = " AND ".join(conditions)

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM inbox_items WHERE {where}",
            params,
        )
        total = (await cursor.fetchone())[0]

        cursor = await conn.execute(
            f"""
            SELECT * FROM inbox_items
            WHERE {where}
            ORDER BY ingested_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        rows = await cursor.fetchall()
        return ([_row_to_dict(r) for r in rows], total)

    async def mark_read(self, inbox_id: int) -> bool:
        """Mark a single inbox item as read."""
        conn = await self.ensure_conn()
        cursor = await conn.execute(
            "UPDATE inbox_items SET is_read = 1 WHERE id = ? AND is_read = 0",
            (inbox_id,),
        )
        await conn.commit()
        return cursor.rowcount > 0

    async def mark_all_read(self, source_type: str | None) -> int:
        """Mark unread current active items as read. Returns updated rows count."""
        conn = await self.ensure_conn()
        if source_type is None:
            cursor = await conn.execute(
                """
                UPDATE inbox_items
                SET is_read = 1
                WHERE is_current = 1 AND status = 'active' AND is_read = 0
                """
            )
        else:
            cursor = await conn.execute(
                """
                UPDATE inbox_items
                SET is_read = 1
                WHERE is_current = 1 AND status = 'active' AND is_read = 0
                  AND source_type = ?
                """,
                (source_type,),
            )
        await conn.commit()
        return cursor.rowcount

    async def get_unread_count(self) -> int:
        """Count unread current active inbox items."""
        conn = await self.ensure_conn()
        cursor = await conn.execute(
            """
            SELECT COUNT(*) FROM inbox_items
            WHERE is_current = 1 AND status = 'active' AND is_read = 0
            """
        )
        row = await cursor.fetchone()
        return int(row[0] if row else 0)

    async def get_cursor(
        self, source_type: str, source_account: str, stream: str
    ) -> str | None:
        """Get cursor value for (source_type, source_account, stream)."""
        conn = await self.ensure_conn()
        cursor = await conn.execute(
            """
            SELECT cursor_value FROM inbox_cursors
            WHERE source_type = ? AND source_account = ? AND stream = ?
            """,
            (source_type, source_account, stream),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_cursor(
        self,
        source_type: str,
        source_account: str,
        stream: str,
        value: str,
    ) -> None:
        """Set cursor value."""
        conn = await self.ensure_conn()
        now = time.time()
        await conn.execute(
            """
            INSERT OR REPLACE INTO inbox_cursors
            (source_type, source_account, stream, cursor_value, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source_type, source_account, stream, value, now),
        )
        await conn.commit()

    async def delete_cursors(self, source_type: str, source_account: str) -> None:
        """Delete all cursor rows for the given source identity."""
        conn = await self.ensure_conn()
        await conn.execute(
            """
            DELETE FROM inbox_cursors
            WHERE source_type = ? AND source_account = ?
            """,
            (source_type, source_account),
        )
        await conn.commit()
