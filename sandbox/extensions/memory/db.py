"""Memory database: SQLite with memories, entities, FTS5, vec_memories. Triggers sync FTS5 on insert/update."""

import logging
from pathlib import Path

import aiosqlite
import sqlite_vec

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
    source_role  VARCHAR(255),
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

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id TEXT NOT NULL REFERENCES memories(id),
    entity_id TEXT NOT NULL REFERENCES entities(id),
    PRIMARY KEY (memory_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_me_entity ON memory_entities(entity_id);
CREATE INDEX IF NOT EXISTS idx_me_memory ON memory_entities(memory_id);

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

CREATE TABLE IF NOT EXISTS memory_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


EMBEDDING_DIMS = 256  # ADR 005 Phase 2; must match embedding extension when used


class MemoryDatabase:
    """SQLite-backed memory store. One connection per instance."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._vec_available: bool = False

    @property
    def vec_available(self) -> bool:
        """True if vec_memories table exists and dimensions match. Vector search disabled otherwise."""
        return self._vec_available

    async def initialize(self) -> None:
        """Create connection and deploy schema."""
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(str(self._db_path))
        # Load sqlite-vec in connection thread (aiosqlite async API)
        await self._conn.enable_load_extension(True)
        await self._conn.load_extension(sqlite_vec.loadable_path())
        await self._conn.enable_load_extension(False)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(_SCHEMA)
        await self._ensure_vec_memories()
        await self._conn.commit()
        await self._migrate_schema()

    async def _ensure_vec_memories(self) -> None:
        """Create vec_memories if dimensions match. Disable vector search on mismatch."""
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT value FROM memory_metadata WHERE key = 'embedding_dimensions'"
        )
        row = await cursor.fetchone()
        stored_dims = int(row[0]) if row and row[0] else None
        expected = EMBEDDING_DIMS
        if stored_dims is None:
            await self._conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                    memory_id TEXT PRIMARY KEY,
                    embedding float[{expected}]
                )
                """
            )
            await self._conn.execute(
                "INSERT OR REPLACE INTO memory_metadata (key, value) VALUES ('embedding_dimensions', ?)",
                (str(expected),),
            )
            self._vec_available = True
        elif stored_dims != expected:
            logger.error(
                "Embedding dimensions mismatch: vec_memories is float[%d] but memory expects %d. "
                "Semantic search disabled. To fix: DROP TABLE vec_memories; DELETE FROM memory_metadata WHERE key='embedding_dimensions'; then restart.",
                stored_dims,
                expected,
            )
            self._vec_available = False
        else:
            self._vec_available = True

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
