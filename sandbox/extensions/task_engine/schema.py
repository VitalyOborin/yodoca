"""Task Engine SQLite schema. Creates agent_task and task_step tables."""

import logging
import sqlite3
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_BUSY_TIMEOUT_MS = 5000

# Migration: ADR 018 task chains — after_task_id, chain_id, chain_order
_MIGRATIONS = [
    "ALTER TABLE agent_task ADD COLUMN after_task_id TEXT REFERENCES agent_task(task_id)",
    "ALTER TABLE agent_task ADD COLUMN chain_id TEXT",
    "ALTER TABLE agent_task ADD COLUMN chain_order INTEGER",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_task (
    task_id       TEXT    PRIMARY KEY,
    parent_id     TEXT    REFERENCES agent_task(task_id),
    run_id        TEXT    NOT NULL,
    agent_id      TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    priority      INTEGER DEFAULT 5,
    payload       TEXT    NOT NULL,
    result        TEXT,
    checkpoint    TEXT,
    error         TEXT,
    attempt_no    INTEGER DEFAULT 0,
    schedule_at   REAL,
    leased_by     TEXT,
    lease_exp     REAL,
    -- strftime('%s') gives second precision; unixepoch('subsec') needs SQLite 3.38+
    created_at    REAL    DEFAULT (cast(strftime('%s','now') as real)),
    updated_at    REAL    DEFAULT (cast(strftime('%s','now') as real))
);

CREATE INDEX IF NOT EXISTS idx_at_status_schedule ON agent_task(status, schedule_at);
CREATE INDEX IF NOT EXISTS idx_at_parent ON agent_task(parent_id);

CREATE TABLE IF NOT EXISTS task_step (
    step_id          TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_no          INTEGER NOT NULL,
    step_type        TEXT NOT NULL,
    status           TEXT NOT NULL,
    idempotency_key  TEXT UNIQUE,
    input_ref        TEXT,
    output_ref       TEXT,
    tokens_used      INTEGER,
    duration_ms      INTEGER,
    error_code       TEXT,
    -- see agent_task comment on strftime vs unixepoch
    created_at       REAL DEFAULT (cast(strftime('%s','now') as real))
);

CREATE INDEX IF NOT EXISTS idx_ts_task ON task_step(task_id, step_no);
"""


async def _run_migrations(conn: aiosqlite.Connection) -> None:
    """Run schema migrations (ADR 018: task chains). Idempotent."""
    for sql in _MIGRATIONS:
        try:
            await conn.execute(sql)
            await conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    # Create indexes for chain columns (idempotent)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_at_after ON agent_task(after_task_id)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_at_chain ON agent_task(chain_id)"
    )
    await conn.commit()


class TaskEngineDb:
    """SQLite connection for Task Engine. One connection per instance."""

    def __init__(self, db_path: Path, busy_timeout: int = _BUSY_TIMEOUT_MS) -> None:
        self._db_path = db_path
        self._busy_timeout = busy_timeout
        self._conn: aiosqlite.Connection | None = None

    async def ensure_conn(self) -> aiosqlite.Connection:
        """Open connection and ensure schema. Idempotent."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(self._db_path))
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            await self._conn.execute(f"PRAGMA busy_timeout={self._busy_timeout}")
            await self._conn.executescript(_SCHEMA)
            await self._conn.commit()
            await _run_migrations(self._conn)
            logger.debug("task_engine: schema ensured at %s", self._db_path)
        return self._conn

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
