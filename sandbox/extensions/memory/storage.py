"""MemoryStorage: CRUD, graph operations, async writer queue. Memory v2."""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class WriteOp:
    """Single write operation for the writer queue."""

    sql: str
    params: tuple
    future: asyncio.Future[Any] | None = None  # None = fire-and-forget


def _escape_fts5_query(q: str) -> str:
    """Sanitize for FTS5: keep only word chars and spaces."""
    q = re.sub(r"[^\w\s]", " ", q, flags=re.UNICODE)
    return " ".join(w for w in q.split() if w)


class MemoryStorage:
    """SQLite-backed memory store with async writer queue. WAL mode, single writer."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._write_conn: aiosqlite.Connection | None = None
        self._read_conn: aiosqlite.Connection | None = None
        self._write_queue: asyncio.Queue[WriteOp] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None
        self._closed = False

    async def initialize(self) -> None:
        """Open connections, load sqlite-vec, deploy schema, start writer task."""
        if self._write_conn is not None:
            return

        self._write_conn = await aiosqlite.connect(str(self._db_path))
        await self._write_conn.enable_load_extension(True)
        try:
            import sqlite_vec

            await self._write_conn.load_extension(sqlite_vec.loadable_path())
        except Exception as e:
            logger.warning("sqlite-vec not available, vector search disabled: %s", e)
        await self._write_conn.enable_load_extension(False)
        await self._write_conn.execute("PRAGMA journal_mode=WAL")
        await self._write_conn.execute("PRAGMA synchronous=NORMAL")

        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        await self._write_conn.executescript(schema_sql)
        await self._write_conn.commit()

        self._read_conn = await aiosqlite.connect(str(self._db_path))
        await self._read_conn.enable_load_extension(True)
        try:
            import sqlite_vec

            await self._read_conn.load_extension(sqlite_vec.loadable_path())
        except Exception:
            pass
        await self._read_conn.enable_load_extension(False)
        await self._read_conn.execute("PRAGMA query_only=ON")

        self._writer_task = asyncio.create_task(self._writer_loop())
        self._closed = False
        logger.info("MemoryStorage initialized: %s", self._db_path)

    async def _writer_loop(self) -> None:
        """Process write operations sequentially."""
        assert self._write_conn is not None
        while not self._closed:
            try:
                op = await asyncio.wait_for(
                    self._write_queue.get(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            if op is None:
                break
            try:
                await self._write_conn.execute(op.sql, op.params)
                await self._write_conn.commit()
                if op.future is not None and not op.future.done():
                    op.future.set_result(None)
            except Exception as e:
                logger.exception("Write op failed: %s", e)
                if op.future is not None and not op.future.done():
                    op.future.set_exception(e)
            finally:
                self._write_queue.task_done()

    async def close(self) -> None:
        """Drain queue, stop writer, close connections."""
        self._closed = True
        if self._writer_task is not None:
            await self._write_queue.put(None)
            try:
                await asyncio.wait_for(self._writer_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._writer_task.cancel()
            self._writer_task = None
        if self._write_conn is not None:
            await self._write_conn.close()
            self._write_conn = None
        if self._read_conn is not None:
            await self._read_conn.close()
            self._read_conn = None

    def _submit_write(self, sql: str, params: tuple, wait: bool = False) -> asyncio.Future[Any] | None:
        """Submit write to queue. If wait=True, returns Future to await."""
        future = asyncio.get_running_loop().create_future() if wait else None
        op = WriteOp(sql=sql, params=params, future=future)
        self._write_queue.put_nowait(op)
        return future

    def insert_node(self, node: dict[str, Any]) -> str:
        """Insert node. Fire-and-forget. Returns node_id (caller must provide in node)."""
        node_id = node.get("id") or str(uuid.uuid4())
        sql = """
            INSERT INTO nodes (
                id, type, content, embedding,
                event_time, created_at, valid_from, valid_until,
                confidence, access_count, last_accessed, decay_rate,
                source_type, source_role, session_id, attributes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            node_id,
            node["type"],
            node["content"],
            node.get("embedding"),
            node["event_time"],
            node["created_at"],
            node["valid_from"],
            node.get("valid_until"),
            node.get("confidence", 1.0),
            node.get("access_count", 0),
            node.get("last_accessed"),
            node.get("decay_rate", 0.1),
            node.get("source_type"),
            node.get("source_role"),
            node.get("session_id"),
            json.dumps(node.get("attributes") or {}),
        )
        self._submit_write(sql, params)
        return node_id

    async def insert_node_awaitable(self, node: dict[str, Any]) -> str:
        """Insert node and await write confirmation. Returns node_id."""
        node_id = node.get("id") or str(uuid.uuid4())
        sql = """
            INSERT INTO nodes (
                id, type, content, embedding,
                event_time, created_at, valid_from, valid_until,
                confidence, access_count, last_accessed, decay_rate,
                source_type, source_role, session_id, attributes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            node_id,
            node["type"],
            node["content"],
            node.get("embedding"),
            node["event_time"],
            node["created_at"],
            node["valid_from"],
            node.get("valid_until"),
            node.get("confidence", 1.0),
            node.get("access_count", 0),
            node.get("last_accessed"),
            node.get("decay_rate", 0.1),
            node.get("source_type"),
            node.get("source_role"),
            node.get("session_id"),
            json.dumps(node.get("attributes") or {}),
        )
        future = self._submit_write(sql, params, wait=True)
        if future:
            await future
        return node_id

    async def soft_delete_node(self, node_id: str) -> None:
        """Soft-delete node: set valid_until = now."""
        now = int(time.time())
        future = self._submit_write(
            "UPDATE nodes SET valid_until = ? WHERE id = ?", (now, node_id), wait=True
        )
        if future:
            await future

    async def update_node_fields(self, node_id: str, fields: dict[str, Any]) -> None:
        """Partial update of node fields (confidence, decay_rate, etc.)."""
        allowed = {"confidence", "decay_rate", "access_count", "last_accessed"}
        updates = [(k, v) for k, v in fields.items() if k in allowed]
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k, _ in updates)
        params = tuple(v for _, v in updates) + (node_id,)
        future = self._submit_write(
            f"UPDATE nodes SET {set_clause} WHERE id = ?", params, wait=True
        )
        if future:
            await future

    def insert_edge(self, edge: dict[str, Any]) -> str:
        """Insert edge. Fire-and-forget. Returns edge_id."""
        edge_id = edge.get("id") or str(uuid.uuid4())
        sql = """
            INSERT INTO edges (
                id, source_id, target_id, relation_type, predicate,
                weight, confidence, valid_from, valid_until, evidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            edge_id,
            edge["source_id"],
            edge["target_id"],
            edge["relation_type"],
            edge.get("predicate"),
            edge.get("weight", 1.0),
            edge.get("confidence", 1.0),
            edge["valid_from"],
            edge.get("valid_until"),
            json.dumps(edge.get("evidence") or []),
            edge["created_at"],
        )
        self._submit_write(sql, params)
        return edge_id

    async def insert_edge_awaitable(self, edge: dict[str, Any]) -> str:
        """Insert edge and await write confirmation. Returns edge_id."""
        edge_id = edge.get("id") or str(uuid.uuid4())
        sql = """
            INSERT INTO edges (
                id, source_id, target_id, relation_type, predicate,
                weight, confidence, valid_from, valid_until, evidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            edge_id,
            edge["source_id"],
            edge["target_id"],
            edge["relation_type"],
            edge.get("predicate"),
            edge.get("weight", 1.0),
            edge.get("confidence", 1.0),
            edge["valid_from"],
            edge.get("valid_until"),
            json.dumps(edge.get("evidence") or []),
            edge["created_at"],
        )
        future = self._submit_write(sql, params, wait=True)
        if future:
            await future
        return edge_id

    async def insert_nodes_batch(self, nodes: list[dict[str, Any]]) -> list[str]:
        """Batch insert nodes. Each is awaitable. Returns list of node_ids."""
        node_ids: list[str] = []
        futures: list[asyncio.Future[Any]] = []
        for node in nodes:
            node_id = node.get("id") or str(uuid.uuid4())
            node_ids.append(node_id)
            sql = """
                INSERT INTO nodes (
                    id, type, content, embedding,
                    event_time, created_at, valid_from, valid_until,
                    confidence, access_count, last_accessed, decay_rate,
                    source_type, source_role, session_id, attributes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                node_id,
                node["type"],
                node["content"],
                node.get("embedding"),
                node["event_time"],
                node["created_at"],
                node["valid_from"],
                node.get("valid_until"),
                node.get("confidence", 1.0),
                node.get("access_count", 0),
                node.get("last_accessed"),
                node.get("decay_rate", 0.1),
                node.get("source_type"),
                node.get("source_role"),
                node.get("session_id"),
                json.dumps(node.get("attributes") or {}),
            )
            future = self._submit_write(sql, params, wait=True)
            if future:
                futures.append(future)
        for f in futures:
            await f
        return node_ids

    async def get_last_episode_id(self, session_id: str) -> str | None:
        """Get the most recent episodic node id for a session."""
        if self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            """
            SELECT id FROM nodes
            WHERE type = 'episodic' AND session_id = ? AND valid_until IS NULL
            ORDER BY event_time DESC LIMIT 1
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def fts_search(
        self,
        query: str,
        *,
        node_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """FTS5 search. Returns list of node dicts."""
        if not query or not query.strip():
            return []
        if self._read_conn is None:
            return []

        fts_query = _escape_fts5_query(query.strip())
        type_filter = ""
        params: list[Any] = [fts_query, limit]
        if node_types:
            placeholders = ",".join("?" * len(node_types))
            type_filter = f" AND n.type IN ({placeholders})"
            params = [fts_query, limit] + list(node_types)

        sql = f"""
            SELECT n.id, n.type, n.content, n.event_time, n.created_at,
                   n.confidence, n.session_id
            FROM nodes n
            INNER JOIN (
                SELECT rowid FROM nodes_fts
                WHERE nodes_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            ) f ON n.rowid = f.rowid
            WHERE n.valid_until IS NULL
            {type_filter}
        """
        cursor = await self._read_conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "type": r[1],
                "content": r[2],
                "event_time": r[3],
                "created_at": r[4],
                "confidence": r[5],
                "session_id": r[6],
            }
            for r in rows
        ]

    def _serialize_embedding(self, embedding: list[float]) -> bytes:
        """Serialize float list to BLOB for sqlite-vec."""
        try:
            import sqlite_vec

            return sqlite_vec.serialize_float32(embedding)
        except ImportError:
            import struct

            return struct.pack("%sf" % len(embedding), *embedding)

    async def save_embedding(self, node_id: str, embedding: list[float]) -> None:
        """Update node embedding and vec_nodes. Awaitable via write queue."""
        blob = self._serialize_embedding(embedding)
        f1 = self._submit_write(
            "UPDATE nodes SET embedding = ? WHERE id = ?", (blob, node_id), wait=True
        )
        f2 = self._submit_write(
            "INSERT OR REPLACE INTO vec_nodes(node_id, embedding) VALUES (?, ?)",
            (node_id, blob),
            wait=True,
        )
        if f1:
            await f1
        if f2:
            await f2

    async def vector_search(
        self,
        query_embedding: list[float],
        *,
        node_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """KNN search via vec_nodes MATCH. Returns same shape as fts_search plus distance."""
        if self._read_conn is None:
            return []
        blob = self._serialize_embedding(query_embedding)
        type_filter = ""
        inner_limit = limit * 5 if node_types else limit
        params: list[Any] = [blob, inner_limit]
        if node_types:
            placeholders = ",".join("?" * len(node_types))
            type_filter = f" AND n.type IN ({placeholders})"
            params = [blob, inner_limit] + list(node_types) + [limit]
        else:
            params = [blob, inner_limit, limit]
        sql = f"""
            SELECT n.id, n.type, n.content, n.event_time, n.created_at,
                   n.confidence, n.session_id, v.distance
            FROM (
                SELECT node_id, distance FROM vec_nodes
                WHERE embedding MATCH ? ORDER BY distance LIMIT ?
            ) v
            INNER JOIN nodes n ON n.id = v.node_id
            WHERE n.valid_until IS NULL
              {type_filter}
            ORDER BY v.distance
            LIMIT ?
        """
        cursor = await self._read_conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "type": r[1],
                "content": r[2],
                "event_time": r[3],
                "created_at": r[4],
                "confidence": r[5],
                "session_id": r[6],
                "distance": r[7],
            }
            for r in rows
        ]

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Fetch node by id."""
        if self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            "SELECT id, type, content, event_time, created_at, confidence FROM nodes WHERE id = ? AND valid_until IS NULL",
            (node_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "type": row[1],
            "content": row[2],
            "event_time": row[3],
            "created_at": row[4],
            "confidence": row[5],
        }

    def ensure_session(self, session_id: str) -> None:
        """Upsert session into sessions_consolidations. Fire-and-forget."""
        import time

        now = int(time.time())
        sql = """
            INSERT INTO sessions_consolidations (session_id, first_seen_at, consolidated_at)
            VALUES (?, ?, NULL)
            ON CONFLICT(session_id) DO NOTHING
        """
        self._submit_write(sql, (session_id, now))

    async def is_session_consolidated(self, session_id: str) -> bool:
        """Check if session was consolidated."""
        if self._read_conn is None:
            return False
        cursor = await self._read_conn.execute(
            "SELECT consolidated_at FROM sessions_consolidations WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row is not None and row[0] is not None

    async def get_session_episodes(
        self,
        session_id: str,
        *,
        limit: int = 30,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Paginated fetch of episodic nodes for a session, ordered by event_time ASC."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, type, content, event_time, created_at, confidence, session_id
            FROM nodes
            WHERE type = 'episodic' AND session_id = ? AND valid_until IS NULL
            ORDER BY event_time ASC
            LIMIT ? OFFSET ?
            """,
            (session_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "type": r[1],
                "content": r[2],
                "event_time": r[3],
                "created_at": r[4],
                "confidence": r[5],
                "session_id": r[6],
            }
            for r in rows
        ]

    async def mark_session_consolidated(self, session_id: str) -> None:
        """Mark session as consolidated. Awaitable via write queue."""
        now = int(time.time())
        future = self._submit_write(
            "UPDATE sessions_consolidations SET consolidated_at = ? WHERE session_id = ?",
            (now, session_id),
            wait=True,
        )
        if future:
            await future

    async def get_unconsolidated_sessions(self) -> list[str]:
        """Return session_ids where consolidated_at IS NULL. For nightly maintenance."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            "SELECT session_id FROM sessions_consolidations WHERE consolidated_at IS NULL"
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def insert_entity(self, entity: dict[str, Any]) -> str:
        """Insert entity. Awaitable. Returns entity_id."""
        entity_id = entity.get("id") or str(uuid.uuid4())
        now = int(time.time())
        sql = """
            INSERT INTO entities (
                id, canonical_name, type, aliases, summary, embedding,
                first_seen, last_updated, mention_count, attributes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            entity_id,
            entity["canonical_name"],
            entity["type"],
            json.dumps(entity.get("aliases") or []),
            entity.get("summary"),
            entity.get("embedding"),
            entity.get("first_seen", now),
            entity.get("last_updated", now),
            entity.get("mention_count", 1),
            json.dumps(entity.get("attributes") or {}),
        )
        future = self._submit_write(sql, params, wait=True)
        if future:
            await future
        return entity_id

    async def get_entity_by_name(self, canonical_name: str) -> dict[str, Any] | None:
        """Case-insensitive lookup by canonical_name."""
        if self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            "SELECT id, canonical_name, type, aliases, summary, mention_count FROM entities WHERE LOWER(canonical_name) = LOWER(?)",
            (canonical_name,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "canonical_name": row[1],
            "type": row[2],
            "aliases": json.loads(row[3]) if row[3] else [],
            "summary": row[4],
            "mention_count": row[5],
        }

    async def search_entity_by_alias(self, alias: str) -> dict[str, Any] | None:
        """Search entities by alias in JSON aliases field."""
        if self._read_conn is None:
            return None
        escaped = alias.replace('"', '""')
        pattern = f'%"{escaped}"%'
        cursor = await self._read_conn.execute(
            "SELECT id, canonical_name, type, aliases, summary, mention_count FROM entities WHERE aliases LIKE ?",
            (pattern,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "canonical_name": row[1],
            "type": row[2],
            "aliases": json.loads(row[3]) if row[3] else [],
            "summary": row[4],
            "mention_count": row[5],
        }

    async def link_node_entity(self, node_id: str, entity_id: str) -> None:
        """Link node to entity via node_entities junction. Awaitable."""
        future = self._submit_write(
            "INSERT OR IGNORE INTO node_entities (node_id, entity_id) VALUES (?, ?)",
            (node_id, entity_id),
            wait=True,
        )
        if future:
            await future

    async def update_entity(
        self, entity_id: str, fields: dict[str, Any]
    ) -> None:
        """Partial update of entity fields. Awaitable."""
        allowed = {"summary", "embedding", "mention_count", "last_updated", "aliases"}
        params_list: list[Any] = []
        set_parts: list[str] = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            set_parts.append(f"{k} = ?")
            params_list.append(
                json.dumps(v) if k == "aliases" and isinstance(v, list) else v
            )
        if not set_parts:
            return
        set_clause = ", ".join(set_parts)
        params = tuple(params_list) + (entity_id,)
        future = self._submit_write(
            f"UPDATE entities SET {set_clause} WHERE id = ?", params, wait=True
        )
        if future:
            await future
