"""Memory database: SQLite with memories, entities, FTS5. Triggers sync FTS5 on insert/update."""

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    content      TEXT NOT NULL,
    session_id   TEXT,
    embedding    BLOB,

    event_time   INTEGER NOT NULL,
    created_at   INTEGER NOT NULL,
    valid_until  INTEGER,

    confidence   REAL DEFAULT 1.0,
    access_count INTEGER DEFAULT 0,
    last_accessed INTEGER,
    decay_rate   REAL DEFAULT 0.1,

    source_ids   TEXT DEFAULT '[]',
    entity_ids   TEXT DEFAULT '[]',
    tags         TEXT DEFAULT '[]',
    attributes   TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);

CREATE TABLE IF NOT EXISTS sessions_consolidations (
    session_id       TEXT PRIMARY KEY,
    first_seen_at    INTEGER NOT NULL,
    consolidated_at  INTEGER
);

CREATE TABLE IF NOT EXISTS entities (
    id             TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    type           TEXT NOT NULL,
    aliases        TEXT DEFAULT '[]',
    summary        TEXT,
    embedding      BLOB,
    mention_count  INTEGER DEFAULT 1,
    protected      INTEGER DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid=rowid,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""


class MemoryDatabase:
    """SQLite-backed memory store. One connection per instance."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create connection and deploy schema."""
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(str(self._db_path))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        await self._migrate_schema()

    async def _migrate_schema(self) -> None:
        """Add session_id to existing memories table if missing (no backward compat)."""
        assert self._conn is not None
        try:
            await self._conn.execute("ALTER TABLE memories ADD COLUMN session_id TEXT")
            await self._conn.commit()
        except Exception:
            pass  # Column already exists or table is new

    async def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            await self.initialize()
        assert self._conn is not None
        return self._conn

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
