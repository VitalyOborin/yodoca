"""MemoryStorage: CRUD, graph operations, async writer queue. Memory v2."""

import asyncio
import json
import logging
import math
import os
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
    params: tuple | list[tuple]
    future: asyncio.Future[Any] | None = None  # None = fire-and-forget
    batch: bool = False  # if True, params is list[tuple] for executemany


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
                if op.batch:
                    await self._write_conn.executemany(op.sql, op.params)
                else:
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
        logger.info("MemoryStorage closing: %s", self._db_path)
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

    def _submit_batch_write(
        self, sql: str, params_list: list[tuple], wait: bool = False
    ) -> asyncio.Future[Any] | None:
        """Submit batch write (executemany) to queue. If wait=True, returns Future to await."""
        future = asyncio.get_running_loop().create_future() if wait else None
        op = WriteOp(sql=sql, params=params_list, future=future, batch=True)
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
        logger.debug("soft_delete_node: %s", node_id[:8])

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

    async def get_decayable_nodes(self) -> list[dict[str, Any]]:
        """Return nodes eligible for decay: decay_rate > 0, valid_until IS NULL, type != episodic."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, confidence, decay_rate, last_accessed, created_at, type
            FROM nodes
            WHERE decay_rate > 0 AND valid_until IS NULL AND type != 'episodic'
            """
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "confidence": r[1],
                "decay_rate": r[2],
                "last_accessed": r[3],
                "created_at": r[4],
                "type": r[5],
            }
            for r in rows
        ]

    async def batch_update_confidence(
        self, updates: list[tuple[str, float]]
    ) -> None:
        """Batch update confidence for nodes. updates: [(node_id, confidence), ...]."""
        if not updates:
            return
        future = self._submit_batch_write(
            "UPDATE nodes SET confidence = ? WHERE id = ?",
            [(c, nid) for nid, c in updates],
            wait=True,
        )
        if future:
            await future

    async def soft_delete_nodes(self, node_ids: list[str]) -> None:
        """Soft-delete nodes: set valid_until = now for given IDs."""
        if not node_ids:
            return
        now = int(time.time())
        params_list = [(now, nid) for nid in node_ids]
        future = self._submit_batch_write(
            "UPDATE nodes SET valid_until = ? WHERE id = ?", params_list, wait=True
        )
        if future:
            await future

    async def record_access_for_nodes(
        self, node_ids: list[str], *, now: int | None = None
    ) -> None:
        """Record access: increment access_count, set last_accessed, apply confidence reinforcement.
        Fire-and-forget writes. Call from retrieval after search results are returned.
        Uses atomic UPDATE for confidence to avoid race with decay/concurrent updates."""
        if not node_ids:
            return
        now = now or int(time.time())
        if self._read_conn is None:
            return
        unique_ids = list(dict.fromkeys(node_ids))
        placeholders = ",".join("?" * len(unique_ids))
        cursor = await self._read_conn.execute(
            f"SELECT id, access_count FROM nodes WHERE id IN ({placeholders}) AND valid_until IS NULL",
            unique_ids,
        )
        rows = await cursor.fetchall()
        updates: list[tuple[int, float, str]] = []
        for r in rows:
            nid, acc = r[0], r[1] or 0
            acc_new = acc + 1
            delta = 0.05 * math.log(1 + acc_new / 20)
            updates.append((now, delta, nid))
        if updates:
            self._submit_batch_write(
                "UPDATE nodes SET access_count = access_count + 1, last_accessed = ?, confidence = MIN(1.0, confidence + ?) WHERE id = ? AND valid_until IS NULL",
                updates,
                wait=False,
            )

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
        logger.debug("insert_nodes_batch: %d nodes", len(nodes))
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

    async def save_entity_embedding(
        self, entity_id: str, embedding: list[float]
    ) -> None:
        """Update entity embedding and vec_entities. Awaitable via write queue."""
        blob = self._serialize_embedding(embedding)
        f1 = self._submit_write(
            "UPDATE entities SET embedding = ? WHERE id = ?", (blob, entity_id), wait=True
        )
        f2 = self._submit_write(
            "INSERT OR REPLACE INTO vec_entities(entity_id, embedding) VALUES (?, ?)",
            (entity_id, blob),
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
            "SELECT id, type, content, event_time, created_at, confidence, access_count, last_accessed FROM nodes WHERE id = ? AND valid_until IS NULL",
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
            "access_count": row[6],
            "last_accessed": row[7],
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
        logger.debug("mark_session_consolidated: %s", session_id)

    async def get_unconsolidated_sessions(self) -> list[str]:
        """Return session_ids where consolidated_at IS NULL. For nightly maintenance."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            "SELECT session_id FROM sessions_consolidations WHERE consolidated_at IS NULL"
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def get_latest_session_id(self) -> str | None:
        """Return the most recent session_id (by first_seen_at). Used to resume after restart."""
        if self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            "SELECT session_id FROM sessions_consolidations ORDER BY first_seen_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return row[0] if row else None

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
            json.dumps(entity.get("aliases") or [], ensure_ascii=False),
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
                json.dumps(v, ensure_ascii=False) if k == "aliases" and isinstance(v, list) else v
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

    async def get_consecutive_episode_pairs(
        self, limit: int = 50
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Pairs of temporally adjacent episodic nodes (cause, effect) without existing causal edge."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, type, content, event_time, session_id
            FROM nodes
            WHERE type = 'episodic' AND valid_until IS NULL AND session_id IS NOT NULL
            ORDER BY session_id, event_time
            """
        )
        rows = await cursor.fetchall()
        by_session: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            rec = {
                "id": r[0],
                "type": r[1],
                "content": r[2],
                "event_time": r[3],
                "session_id": r[4],
            }
            by_session.setdefault(r[4], []).append(rec)
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for episodes in by_session.values():
            for i in range(len(episodes) - 1):
                pairs.append((episodes[i], episodes[i + 1]))
                if len(pairs) >= limit:
                    break
            if len(pairs) >= limit:
                break
        if not pairs:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT source_id, target_id FROM edges
            WHERE relation_type = 'causal' AND valid_until IS NULL
            """
        )
        existing = {(r[0], r[1]) for r in await cursor.fetchall()}
        return [
            (prev, curr)
            for prev, curr in pairs
            if (prev["id"], curr["id"]) not in existing
        ][:limit]

    async def get_entities_needing_enrichment(
        self, min_mentions: int = 3
    ) -> list[dict[str, Any]]:
        """Entities with sparse summary and enough mentions for enrichment."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, canonical_name, type, aliases, summary, mention_count
            FROM entities
            WHERE (summary IS NULL OR summary = '') AND mention_count >= ?
            ORDER BY mention_count DESC
            """,
            (min_mentions,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "canonical_name": r[1],
                "type": r[2],
                "aliases": json.loads(r[3]) if r[3] else [],
                "summary": r[4],
                "mention_count": r[5],
            }
            for r in rows
        ]

    async def temporal_chain_traversal(
        self,
        seed_node_ids: list[str],
        *,
        direction: str = "forward",
        max_depth: int = 3,
        limit: int = 20,
        event_after: int | None = None,
        event_before: int | None = None,
    ) -> list[dict[str, Any]]:
        """Follow temporal edges from seed nodes. direction: forward (targets) or backward (sources)."""
        if not self._read_conn or not seed_node_ids:
            return []
        seeds = seed_node_ids[:10]
        placeholders = ",".join("?" * len(seeds))
        time_filter = ""
        time_params: list[Any] = []
        if event_after is not None:
            time_filter += " AND n.event_time >= ?"
            time_params.append(event_after)
        if event_before is not None:
            time_filter += " AND n.event_time <= ?"
            time_params.append(event_before)

        if direction == "forward":
            join_col, follow_col = "source_id", "target_id"
        else:
            join_col, follow_col = "target_id", "source_id"

        sql = f"""
            WITH RECURSIVE chain(node_id, depth) AS (
                SELECT id, 0 FROM nodes WHERE id IN ({placeholders}) AND valid_until IS NULL
                UNION ALL
                SELECT e.{follow_col}, c.depth + 1 FROM edges e
                JOIN chain c ON e.{join_col} = c.node_id
                WHERE e.relation_type = 'temporal' AND e.valid_until IS NULL
                  AND c.depth < ?
            )
            SELECT DISTINCT n.id, n.type, n.content, n.event_time, n.created_at,
                   n.confidence, n.session_id
            FROM nodes n
            JOIN chain c ON n.id = c.node_id
            WHERE n.valid_until IS NULL {time_filter}
            ORDER BY n.event_time ASC
            LIMIT ?
        """
        params: list[Any] = list(seeds) + [max_depth] + time_params + [limit]
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

    async def causal_chain_traversal(
        self,
        seed_node_id: str,
        *,
        max_depth: int = 3,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """BFS following causal edges: source=cause, target=effect. Traverse from effect to causes."""
        if not self._read_conn:
            return []
        sql = """
            WITH RECURSIVE causal_chain(node_id, depth) AS (
                SELECT source_id, 1 FROM edges
                WHERE target_id = ? AND relation_type = 'causal'
                  AND valid_until IS NULL
                UNION ALL
                SELECT e.source_id, cc.depth + 1 FROM edges e
                JOIN causal_chain cc ON e.target_id = cc.node_id
                WHERE e.relation_type = 'causal'
                  AND e.valid_until IS NULL
                  AND cc.depth < ?
            )
            SELECT DISTINCT n.id, n.type, n.content, n.event_time, n.created_at,
                   n.confidence, n.session_id
            FROM nodes n
            JOIN causal_chain cc ON n.id = cc.node_id
            WHERE n.valid_until IS NULL
            ORDER BY n.event_time DESC
            LIMIT ?
        """
        cursor = await self._read_conn.execute(
            sql, (seed_node_id, max_depth, limit)
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

    async def entity_nodes_for_entity(
        self,
        entity_id: str,
        *,
        node_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Nodes linked to entity via node_entities."""
        if not self._read_conn:
            return []
        type_filter = ""
        params: list[Any] = [entity_id, limit]
        if node_types:
            placeholders = ",".join("?" * len(node_types))
            type_filter = f" AND n.type IN ({placeholders})"
            params = [entity_id] + list(node_types) + [limit]
        sql = f"""
            SELECT n.id, n.type, n.content, n.event_time, n.created_at,
                   n.confidence, n.session_id
            FROM nodes n
            INNER JOIN node_entities ne ON n.id = ne.node_id
            WHERE ne.entity_id = ? AND n.valid_until IS NULL {type_filter}
            ORDER BY n.event_time DESC
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
            }
            for r in rows
        ]

    async def get_timeline(
        self,
        *,
        entity_id: str | None = None,
        event_after: int | None = None,
        event_before: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get chronological episodic events. Optional entity and time range filters."""
        if not self._read_conn:
            return []
        conditions = ["n.type = 'episodic'", "n.valid_until IS NULL"]
        params: list[Any] = []
        if entity_id:
            conditions.append("n.id IN (SELECT node_id FROM node_entities WHERE entity_id = ?)")
            params.append(entity_id)
        if event_after is not None:
            conditions.append("n.event_time >= ?")
            params.append(event_after)
        if event_before is not None:
            conditions.append("n.event_time <= ?")
            params.append(event_before)
        where = " AND ".join(conditions)
        params.append(limit)
        cursor = await self._read_conn.execute(
            f"""
            SELECT n.id, n.type, n.content, n.event_time, n.created_at,
                   n.confidence, n.session_id
            FROM nodes n
            WHERE {where}
            ORDER BY n.event_time ASC
            LIMIT ?
            """,
            params,
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

    async def get_nodes_by_ids(self, node_ids: list[str]) -> list[dict[str, Any]]:
        """Batch fetch nodes by ID. Preserves order where possible."""
        if not self._read_conn or not node_ids:
            return []
        seen: set[str] = set()
        order: list[str] = []
        for nid in node_ids:
            if nid not in seen:
                seen.add(nid)
                order.append(nid)
        placeholders = ",".join("?" * len(order))
        cursor = await self._read_conn.execute(
            f"""
            SELECT id, type, content, event_time, created_at, confidence, session_id
            FROM nodes WHERE id IN ({placeholders}) AND valid_until IS NULL
            """,
            tuple(order),
        )
        rows = await cursor.fetchall()
        by_id = {
            r[0]: {
                "id": r[0],
                "type": r[1],
                "content": r[2],
                "event_time": r[3],
                "created_at": r[4],
                "confidence": r[5],
                "session_id": r[6],
            }
            for r in rows
        }
        return [by_id[nid] for nid in order if nid in by_id]

    async def get_entities_for_nodes(
        self, node_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Entities linked to these nodes via node_entities. Deduplicated by entity id."""
        if not self._read_conn or not node_ids:
            return []
        placeholders = ",".join("?" * len(node_ids))
        cursor = await self._read_conn.execute(
            f"""
            SELECT DISTINCT e.id, e.canonical_name, e.type, e.aliases, e.summary, e.mention_count
            FROM entities e
            INNER JOIN node_entities ne ON e.id = ne.entity_id
            WHERE ne.node_id IN ({placeholders})
            """,
            tuple(node_ids),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "canonical_name": row[1],
                "type": row[2],
                "aliases": json.loads(row[3]) if row[3] else [],
                "summary": row[4],
                "mention_count": row[5],
            }
            for row in rows
        ]

    async def get_derived_from_targets(self, node_id: str) -> list[str]:
        """Target node IDs for derived_from edges from this node (source -> target)."""
        if not self._read_conn:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT target_id FROM edges
            WHERE source_id = ? AND relation_type = 'derived_from'
              AND valid_until IS NULL
            """,
            (node_id,),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def get_graph_stats(self) -> dict[str, Any]:
        """Graph-level metrics: node counts by type, edge counts by relation_type, entities, orphans, avg edges/node."""
        if self._read_conn is None:
            return {
                "nodes": {"episodic": 0, "semantic": 0, "procedural": 0, "opinion": 0},
                "edges": {"temporal": 0, "causal": 0, "entity": 0, "derived_from": 0, "supersedes": 0},
                "entities": 0,
                "orphan_nodes": 0,
                "avg_edges_per_node": 0.0,
            }
        node_counts: dict[str, int] = {"episodic": 0, "semantic": 0, "procedural": 0, "opinion": 0}
        cursor = await self._read_conn.execute(
            "SELECT type, COUNT(*) FROM nodes WHERE valid_until IS NULL GROUP BY type"
        )
        for row in await cursor.fetchall():
            node_counts[row[0]] = row[1]
        edge_counts: dict[str, int] = {
            "temporal": 0,
            "causal": 0,
            "entity": 0,
            "derived_from": 0,
            "supersedes": 0,
        }
        cursor = await self._read_conn.execute(
            "SELECT relation_type, COUNT(*) FROM edges WHERE valid_until IS NULL GROUP BY relation_type"
        )
        for row in await cursor.fetchall():
            if row[0] in edge_counts:
                edge_counts[row[0]] = row[1]
        cursor = await self._read_conn.execute("SELECT COUNT(*) FROM entities")
        entity_count = (await cursor.fetchone())[0]
        cursor = await self._read_conn.execute(
            """
            SELECT COUNT(*) FROM nodes n
            WHERE n.valid_until IS NULL
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_id = n.id AND e.valid_until IS NULL)
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.target_id = n.id AND e.valid_until IS NULL)
            """
        )
        orphan_count = (await cursor.fetchone())[0]
        total_nodes = sum(node_counts.values())
        total_edges = sum(edge_counts.values())
        avg_edges = total_edges / total_nodes if total_nodes > 0 else 0.0
        return {
            "nodes": node_counts,
            "edges": edge_counts,
            "entities": entity_count,
            "orphan_nodes": orphan_count,
            "avg_edges_per_node": round(avg_edges, 1),
        }

    def get_storage_size_mb(self) -> float:
        """Get database file size in MB."""
        if not self._db_path or not self._db_path.exists():
            return 0.0
        return round(os.path.getsize(self._db_path) / (1024 * 1024), 1)

    async def get_provenance_chain(self, node_id: str) -> dict[str, Any]:
        """Provenance chain: node, source episodes (derived_from), supersedes/superseded_by, linked entities."""
        node = await self.get_node(node_id)
        if not node:
            return {"node": None, "source_episodes": [], "supersedes": [], "superseded_by": [], "entities": []}
        source_ids = await self.get_derived_from_targets(node_id)
        source_episodes = await self.get_nodes_by_ids(source_ids) if source_ids else []
        supersedes_ids = await self._get_supersedes_targets(node_id)
        superseded_by_ids = await self._get_supersedes_sources(node_id)
        supersedes = await self.get_nodes_by_ids(supersedes_ids) if supersedes_ids else []
        superseded_by = await self.get_nodes_by_ids(superseded_by_ids) if superseded_by_ids else []
        entities = await self.get_entities_for_nodes([node_id])
        return {
            "node": node,
            "source_episodes": source_episodes,
            "supersedes": supersedes,
            "superseded_by": superseded_by,
            "entities": entities,
        }

    async def _get_supersedes_targets(self, node_id: str) -> list[str]:
        """Node IDs that this node supersedes (source_id=node_id, relation_type=supersedes)."""
        if not self._read_conn:
            return []
        cursor = await self._read_conn.execute(
            "SELECT target_id FROM edges WHERE source_id = ? AND relation_type = 'supersedes' AND valid_until IS NULL",
            (node_id,),
        )
        return [r[0] for r in await cursor.fetchall()]

    async def _get_supersedes_sources(self, node_id: str) -> list[str]:
        """Node IDs that supersede this node (target_id=node_id, relation_type=supersedes)."""
        if not self._read_conn:
            return []
        cursor = await self._read_conn.execute(
            "SELECT source_id FROM edges WHERE target_id = ? AND relation_type = 'supersedes' AND valid_until IS NULL",
            (node_id,),
        )
        return [r[0] for r in await cursor.fetchall()]

    async def get_weak_nodes(
        self, threshold: float = 0.3, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Nodes with low confidence (non-episodic), ordered by confidence ASC."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, type, content, confidence, last_accessed
            FROM nodes
            WHERE valid_until IS NULL AND type != 'episodic' AND confidence < ?
            ORDER BY confidence ASC
            LIMIT ?
            """,
            (threshold, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "type": r[1],
                "content": r[2],
                "confidence": r[3],
                "last_accessed": r[4],
            }
            for r in rows
        ]
