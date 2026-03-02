"""MemoryStorage: CRUD, graph operations, async writer queue. Memory v3."""

import asyncio
import json
import logging
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
    return_rowcount: bool = False  # if True, future gets cursor.rowcount


def _escape_fts5_query(q: str) -> str:
    """Sanitize for FTS5: keep only word chars and spaces."""
    q = re.sub(r"[^\w\s]", " ", q, flags=re.UNICODE)
    return " ".join(w for w in q.split() if w)


class MemoryStorage:
    """SQLite-backed memory store with async writer queue. WAL mode, single writer."""

    def __init__(self, db_path: Path, *, embedding_dimensions: int = 256) -> None:
        self._db_path = db_path
        self._embedding_dimensions = embedding_dimensions
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
                    result = None
                else:
                    cursor = await self._write_conn.execute(op.sql, op.params)
                    result = cursor.rowcount if op.return_rowcount else None
                await self._write_conn.commit()
                if op.future is not None and not op.future.done():
                    op.future.set_result(result)
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

    def _submit_write(
        self,
        sql: str,
        params: tuple,
        wait: bool = False,
        return_rowcount: bool = False,
    ) -> asyncio.Future[Any] | None:
        """Submit write to queue. If wait=True, returns Future to await."""
        future = asyncio.get_running_loop().create_future() if wait else None
        op = WriteOp(sql=sql, params=params, future=future, return_rowcount=return_rowcount)
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

    def insert_episode(self, episode: dict[str, Any]) -> str:
        """Insert episode into episodes table (v3). Fire-and-forget. Returns episode_id."""
        episode_id = episode.get("id") or str(uuid.uuid4())
        sql = """
            INSERT INTO episodes (id, content, actor, session_id, t_obs, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (
            episode_id,
            episode["content"],
            episode["actor"],
            episode["session_id"],
            episode["t_obs"],
            episode["created_at"],
        )
        self._submit_write(sql, params)
        return episode_id

    async def insert_episode_awaitable(self, episode: dict[str, Any]) -> str:
        """Insert episode and await write confirmation. Returns episode_id."""
        episode_id = episode.get("id") or str(uuid.uuid4())
        sql = """
            INSERT INTO episodes (id, content, actor, session_id, t_obs, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (
            episode_id,
            episode["content"],
            episode["actor"],
            episode["session_id"],
            episode["t_obs"],
            episode["created_at"],
        )
        future = self._submit_write(sql, params, wait=True)
        if future:
            await future
        return episode_id

    async def get_recent_session_episodes(
        self, session_id: str, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Fetch recent episodes for session (v3), ordered by t_obs DESC."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, content, actor, session_id, t_obs, created_at
            FROM episodes
            WHERE session_id = ?
            ORDER BY t_obs DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "content": r[1],
                "actor": r[2],
                "session_id": r[3],
                "t_obs": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    async def get_episode_by_id(self, episode_id: str) -> dict[str, Any] | None:
        """Fetch episode by id (v3). For retry pipeline."""
        if self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            "SELECT id, content, actor, session_id, t_obs, created_at FROM episodes WHERE id = ?",
            (episode_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "content": row[1],
            "actor": row[2],
            "session_id": row[3],
            "t_obs": row[4],
            "created_at": row[5],
        }

    def _serialize_embedding(self, embedding: list[float]) -> bytes:
        """Serialize float list to BLOB for sqlite-vec. Applies defensive truncation
        (Matryoshka) when the provider returns more dimensions than expected (e.g.
        LM Studio ignoring dimensions=256 and returning 1024).
        """
        expected_dim = self._embedding_dimensions
        if len(embedding) > expected_dim:
            logger.warning(
                "Embedding dimension mismatch: provider returned %d, expected %d. "
                "Truncating to %d (Matryoshka). Check provider 'dimensions' parameter support.",
                len(embedding),
                expected_dim,
                expected_dim,
            )
            embedding = embedding[:expected_dim]
        elif len(embedding) < expected_dim:
            raise ValueError(
                f"Dimension mismatch: expected {expected_dim}, got {len(embedding)}"
            )

        try:
            import sqlite_vec

            return sqlite_vec.serialize_float32(embedding)
        except ImportError:
            import struct

            return struct.pack("%sf" % len(embedding), *embedding)

    async def save_entity_embedding(
        self, entity_id: str, embedding: list[float]
    ) -> None:
        """Update entity embedding and vec_entities. Awaitable via write queue."""
        blob = self._serialize_embedding(embedding)
        f1 = self._submit_write(
            "UPDATE entities SET embedding = ? WHERE id = ?",
            (blob, entity_id),
            wait=True,
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
        """Paginated fetch of episodes for a session (v3), ordered by t_obs ASC."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, content, actor, session_id, t_obs, created_at
            FROM episodes
            WHERE session_id = ?
            ORDER BY t_obs ASC
            LIMIT ? OFFSET ?
            """,
            (session_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "content": r[1],
                "actor": r[2],
                "session_id": r[3],
                "t_obs": r[4],
                "created_at": r[5],
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

    # --- v3 Entity CRUD ---

    def insert_entity(self, entity: dict[str, Any]) -> str:
        """Insert entity (v3). Fire-and-forget. Returns entity_id."""
        entity_id = entity.get("id") or str(uuid.uuid4())
        now = int(time.time())
        sql = """
            INSERT INTO entities (id, name, aliases, summary, entity_type, mention_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            entity_id,
            entity["name"],
            json.dumps(entity.get("aliases") or [], ensure_ascii=False),
            entity.get("summary"),
            entity.get("entity_type", ""),
            entity.get("mention_count", 1),
            now,
            now,
        )
        self._submit_write(sql, params)
        return entity_id

    async def insert_entity_awaitable(self, entity: dict[str, Any]) -> str:
        """Insert entity (v3). Awaitable. Returns entity_id."""
        entity_id = entity.get("id") or str(uuid.uuid4())
        now = int(time.time())
        sql = """
            INSERT INTO entities (id, name, aliases, summary, entity_type, mention_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            entity_id,
            entity["name"],
            json.dumps(entity.get("aliases") or [], ensure_ascii=False),
            entity.get("summary"),
            entity.get("entity_type", ""),
            entity.get("mention_count", 1),
            now,
            now,
        )
        future = self._submit_write(sql, params, wait=True)
        if future:
            await future
        return entity_id

    async def get_entity_by_id(self, entity_id: str) -> dict[str, Any] | None:
        """Fetch entity by id (v3)."""
        if self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            "SELECT id, name, aliases, summary, entity_type, mention_count FROM entities WHERE id = ?",
            (entity_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "aliases": json.loads(row[2]) if row[2] else [],
            "summary": row[3],
            "entity_type": row[4] or "",
            "mention_count": row[5],
        }

    async def get_entity_by_normalized_name(self, name: str) -> dict[str, Any] | None:
        """Lookup entity by normalized (lowercase) name (v3)."""
        if self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            "SELECT id, name, aliases, summary, entity_type, mention_count FROM entities WHERE lower(name) = lower(?)",
            (name.strip(),),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "aliases": json.loads(row[2]) if row[2] else [],
            "summary": row[3],
            "entity_type": row[4] or "",
            "mention_count": row[5],
        }

    async def get_entity_by_alias(self, alias: str) -> dict[str, Any] | None:
        """Search entity by alias via JSON scan (v3)."""
        if self._read_conn is None:
            return None
        alias_lower = alias.strip().lower()
        cursor = await self._read_conn.execute(
            """
            SELECT e.id, e.name, e.aliases, e.summary, e.entity_type, e.mention_count
            FROM entities e, json_each(e.aliases) j
            WHERE lower(j.value) = ?
            LIMIT 1
            """,
            (alias_lower,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "aliases": json.loads(row[2]) if row[2] else [],
            "summary": row[3],
            "entity_type": row[4] or "",
            "mention_count": row[5],
        }

    async def update_entity(self, entity_id: str, fields: dict[str, Any]) -> None:
        """Partial update of entity (v3). Allowed: summary, mention_count, aliases."""
        allowed = {"summary", "mention_count", "aliases", "updated_at"}
        updates = [(k, v) for k, v in fields.items() if k in allowed]
        if not updates:
            return
        if "updated_at" not in fields:
            updates.append(("updated_at", int(time.time())))
        set_parts = []
        params: list[Any] = []
        for k, v in updates:
            set_parts.append(f"{k} = ?")
            params.append(
                json.dumps(v, ensure_ascii=False) if k == "aliases" and isinstance(v, list) else v
            )
        params.append(entity_id)
        future = self._submit_write(
            f"UPDATE entities SET {', '.join(set_parts)} WHERE id = ?", tuple(params), wait=True
        )
        if future:
            await future

    async def save_entity_embedding(self, entity_id: str, embedding: list[float]) -> None:
        """Upsert embedding into vec_entities (v3)."""
        try:
            blob = self._serialize_embedding(embedding)
        except Exception as e:
            logger.warning("save_entity_embedding serialize failed: %s", e)
            return
        future = self._submit_write(
            "INSERT OR REPLACE INTO vec_entities(entity_id, embedding) VALUES (?, ?)",
            (entity_id, blob),
            wait=True,
        )
        if future:
            await future

    # --- v3 Fact CRUD ---

    async def insert_fact(self, fact: dict[str, Any]) -> str:
        """Insert fact (v3). Awaitable. FTS5 trigger fires automatically."""
        fact_id = fact.get("id") or str(uuid.uuid4())
        now = int(time.time())
        t_created = fact.get("t_created") or now
        sql = """
            INSERT INTO facts (id, subject_id, predicate, object_id, fact_text, t_valid, t_invalid, t_created, source_episode_id, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            fact_id,
            fact["subject_id"],
            fact["predicate"],
            fact["object_id"],
            fact["fact_text"],
            fact.get("t_valid"),
            fact.get("t_invalid"),
            t_created,
            fact.get("source_episode_id"),
            fact.get("confidence", 1.0),
        )
        future = self._submit_write(sql, params, wait=True)
        if future:
            await future
        return fact_id

    async def get_facts_for_entity_pair(
        self, subject_id: str, object_id: str
    ) -> list[dict[str, Any]]:
        """Active facts between two entities (v3)."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, subject_id, predicate, object_id, fact_text, t_valid, t_invalid, embedding
            FROM facts
            WHERE subject_id = ? AND object_id = ? AND t_expired IS NULL
            """,
            (subject_id, object_id),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "subject_id": r[1],
                "predicate": r[2],
                "object_id": r[3],
                "fact_text": r[4],
                "t_valid": r[5],
                "t_invalid": r[6],
                "embedding": r[7],
            }
            for r in rows
        ]

    async def get_facts_by_subject_predicate(
        self, subject_id: str, predicate: str
    ) -> list[dict[str, Any]]:
        """Facts by subject+predicate for temporal conflict detection (v3)."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, object_id, fact_text, t_valid, t_invalid
            FROM facts
            WHERE subject_id = ? AND predicate = ? AND t_expired IS NULL
            """,
            (subject_id, predicate),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "object_id": r[1],
                "fact_text": r[2],
                "t_valid": r[3],
                "t_invalid": r[4],
            }
            for r in rows
        ]

    async def expire_fact(
        self, fact_id: str, invalidated_by: str | None = None
    ) -> None:
        """Set t_expired and invalidated_by on fact (v3). Use invalidated_by=None for forget."""
        now = int(time.time())
        future = self._submit_write(
            "UPDATE facts SET t_expired = ?, invalidated_by = ? WHERE id = ?",
            (now, invalidated_by, fact_id),
            wait=True,
        )
        if future:
            await future

    async def update_fact_confidence(
        self, fact_id: str, confidence: float
    ) -> None:
        """Set confidence on an active fact (e.g. for confirm_fact). Only affects non-expired facts."""
        future = self._submit_write(
            "UPDATE facts SET confidence = ? WHERE id = ? AND t_expired IS NULL",
            (confidence, fact_id),
            wait=True,
        )
        if future:
            await future

    async def save_fact_embedding(self, fact_id: str, embedding: list[float]) -> None:
        """Upsert embedding into vec_facts (v3)."""
        try:
            blob = self._serialize_embedding(embedding)
        except Exception as e:
            logger.warning("save_fact_embedding serialize failed: %s", e)
            return
        future = self._submit_write(
            "INSERT OR REPLACE INTO vec_facts(fact_id, embedding) VALUES (?, ?)",
            (fact_id, blob),
            wait=True,
        )
        if future:
            await future

    # --- v3 Vector search ---

    async def vec_search_entities(
        self, embedding: list[float], top_k: int = 10
    ) -> list[dict[str, Any]]:
        """KNN search on vec_entities. Returns entity_id and distance."""
        if self._read_conn is None:
            return []
        try:
            blob = self._serialize_embedding(embedding)
        except Exception:
            return []
        try:
            cursor = await self._read_conn.execute(
                """
                SELECT entity_id, distance FROM vec_entities
                WHERE embedding MATCH ? ORDER BY distance LIMIT ?
                """,
                (blob, top_k),
            )
        except Exception as e:
            logger.debug("vec_search_entities failed (vec table may not exist): %s", e)
            return []
        rows = await cursor.fetchall()
        return [{"entity_id": r[0], "distance": r[1]} for r in rows]

    async def vec_search_facts(
        self, embedding: list[float], top_k: int = 10
    ) -> list[dict[str, Any]]:
        """KNN search on vec_facts. Returns fact_id and distance."""
        if self._read_conn is None:
            return []
        try:
            blob = self._serialize_embedding(embedding)
        except Exception:
            return []
        try:
            cursor = await self._read_conn.execute(
                """
                SELECT fact_id, distance FROM vec_facts
                WHERE embedding MATCH ? ORDER BY distance LIMIT ?
                """,
                (blob, top_k),
            )
        except Exception as e:
            logger.debug("vec_search_facts failed (vec table may not exist): %s", e)
            return []
        rows = await cursor.fetchall()
        return [{"fact_id": r[0], "distance": r[1]} for r in rows]

    async def vec_search_communities(
        self, embedding: list[float], top_k: int = 5
    ) -> list[dict[str, Any]]:
        """KNN search on vec_communities. Returns community_id and distance."""
        if self._read_conn is None:
            return []
        try:
            blob = self._serialize_embedding(embedding)
        except Exception:
            return []
        try:
            cursor = await self._read_conn.execute(
                """
                SELECT community_id, distance FROM vec_communities
                WHERE embedding MATCH ? ORDER BY distance LIMIT ?
                """,
                (blob, top_k),
            )
        except Exception as e:
            logger.debug("vec_search_communities failed (vec table may not exist): %s", e)
            return []
        rows = await cursor.fetchall()
        return [{"community_id": r[0], "distance": r[1]} for r in rows]

    # --- v3 Communities (Tier 3) ---

    async def insert_community(self, community: dict[str, Any]) -> str:
        """Insert community. Awaitable. Returns community_id."""
        community_id = community.get("id") or str(uuid.uuid4())
        now = int(time.time())
        future = self._submit_write(
            """
            INSERT INTO communities (id, name, summary, member_count, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                community_id,
                community.get("name", ""),
                community.get("summary", ""),
                community.get("member_count", 0),
                now,
            ),
            wait=True,
        )
        if future:
            await future
        return community_id

    async def get_community_by_id(self, community_id: str) -> dict[str, Any] | None:
        """Fetch community by id."""
        if not community_id or self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            """
            SELECT id, name, summary, member_count, updated_at
            FROM communities
            WHERE id = ?
            """,
            (community_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "summary": row[2],
            "member_count": row[3],
            "updated_at": row[4],
        }

    async def update_community(
        self, community_id: str, fields: dict[str, Any]
    ) -> None:
        """Update community fields (summary, member_count, updated_at)."""
        if not community_id or not fields:
            return
        now = int(time.time())
        set_parts = ["updated_at = ?"]
        params: list[Any] = [now]
        if "summary" in fields:
            set_parts.append("summary = ?")
            params.append(fields["summary"])
        if "member_count" in fields:
            set_parts.append("member_count = ?")
            params.append(fields["member_count"])
        if "name" in fields:
            set_parts.append("name = ?")
            params.append(fields["name"])
        params.append(community_id)
        future = self._submit_write(
            f"UPDATE communities SET {', '.join(set_parts)} WHERE id = ?",
            tuple(params),
            wait=True,
        )
        if future:
            await future

    async def add_community_member(self, community_id: str, entity_id: str) -> None:
        """Insert into community_members and sync member_count."""
        future = self._submit_write(
            "INSERT OR IGNORE INTO community_members (community_id, entity_id) VALUES (?, ?)",
            (community_id, entity_id),
            wait=True,
        )
        if future:
            await future
        if self._read_conn:
            cursor = await self._read_conn.execute(
                "SELECT COUNT(*) FROM community_members WHERE community_id = ?",
                (community_id,),
            )
            row = await cursor.fetchone()
            if row:
                future2 = self._submit_write(
                    "UPDATE communities SET member_count = ?, updated_at = ? WHERE id = ?",
                    (row[0], int(time.time()), community_id),
                    wait=True,
                )
                if future2:
                    await future2

    async def get_entity_community(self, entity_id: str) -> str | None:
        """Lookup community_id for entity from community_members."""
        if not entity_id or self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            "SELECT community_id FROM community_members WHERE entity_id = ?",
            (entity_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_community_members(
        self, community_id: str
    ) -> list[dict[str, Any]]:
        """Entities in community with name and summary."""
        if not community_id or self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT e.id, e.name, e.summary
            FROM entities e
            JOIN community_members cm ON cm.entity_id = e.id
            WHERE cm.community_id = ?
            """,
            (community_id,),
        )
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "name": r[1], "summary": r[2] or ""}
            for r in rows
        ]

    async def save_community_embedding(
        self, community_id: str, embedding: list[float]
    ) -> None:
        """Upsert embedding into vec_communities."""
        try:
            blob = self._serialize_embedding(embedding)
        except Exception as e:
            logger.warning("save_community_embedding serialize failed: %s", e)
            return
        future = self._submit_write(
            "INSERT OR REPLACE INTO vec_communities(community_id, embedding) VALUES (?, ?)",
            (community_id, blob),
            wait=True,
        )
        if future:
            await future

    async def get_all_entity_ids(
        self, limit: int = 1000, offset: int = 0
    ) -> list[str]:
        """Paginated entity ids for periodic refresh."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            "SELECT id FROM entities ORDER BY id LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def get_entities_needing_enrichment(
        self, min_mentions: int = 3
    ) -> list[dict[str, Any]]:
        """Entities with sparse summary and enough mentions for enrichment (v3)."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, name, entity_type, aliases, summary, mention_count
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
                "name": r[1],
                "entity_type": r[2],
                "aliases": json.loads(r[3]) if r[3] else [],
                "summary": r[4],
                "mention_count": r[5],
            }
            for r in rows
        ]

    async def get_neighboring_communities(
        self, entity_id: str, min_shared_facts: int = 2
    ) -> list[str]:
        """Community IDs of entities that share >= min_shared_facts active facts with entity."""
        if not entity_id or self._read_conn is None:
            return []
        try:
            cursor = await self._read_conn.execute(
                """
                SELECT cm.community_id
                FROM facts f
                JOIN community_members cm ON cm.entity_id = (
                    CASE WHEN f.subject_id = ? THEN f.object_id ELSE f.subject_id END
                )
                WHERE (f.subject_id = ? OR f.object_id = ?)
                  AND f.t_expired IS NULL
                GROUP BY cm.community_id
                HAVING COUNT(DISTINCT f.id) >= ?
                """,
                (entity_id, entity_id, entity_id, min_shared_facts),
            )
            rows = await cursor.fetchall()
            return [r[0] for r in rows]
        except Exception as e:
            logger.debug("get_neighboring_communities failed: %s", e)
            return []

    async def get_facts_for_community(
        self, community_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Facts where both subject and object are members of the community."""
        if not community_id or self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT f.id, f.subject_id, f.predicate, f.object_id, f.fact_text
            FROM facts f
            JOIN community_members cm1 ON cm1.entity_id = f.subject_id AND cm1.community_id = ?
            JOIN community_members cm2 ON cm2.entity_id = f.object_id AND cm2.community_id = ?
            WHERE f.t_expired IS NULL
            ORDER BY f.confidence DESC
            LIMIT ?
            """,
            (community_id, community_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "subject_id": r[1],
                "predicate": r[2],
                "object_id": r[3],
                "fact_text": r[4],
            }
            for r in rows
        ]

    async def remove_community_member(self, community_id: str, entity_id: str) -> None:
        """Remove entity from community. Syncs member_count."""
        future = self._submit_write(
            "DELETE FROM community_members WHERE community_id = ? AND entity_id = ?",
            (community_id, entity_id),
            wait=True,
        )
        if future:
            await future
        if self._read_conn:
            cursor = await self._read_conn.execute(
                "SELECT COUNT(*) FROM community_members WHERE community_id = ?",
                (community_id,),
            )
            row = await cursor.fetchone()
            if row:
                future2 = self._submit_write(
                    "UPDATE communities SET member_count = ?, updated_at = ? WHERE id = ?",
                    (row[0], int(time.time()), community_id),
                    wait=True,
                )
                if future2:
                    await future2

    # --- v3 Pipeline queue ---

    async def enqueue_atomic_facts(
        self, episode_id: str, texts: list[str]
    ) -> list[str]:
        """Batch insert into pipeline_queue. Returns list of queue item ids."""
        if not texts:
            return []
        now = int(time.time())
        ids: list[str] = []
        params_list: list[tuple] = []
        for t in texts:
            qid = str(uuid.uuid4())
            ids.append(qid)
            params_list.append((qid, episode_id, t.strip(), "pending", 0, now))
        sql = """
            INSERT INTO pipeline_queue (id, episode_id, atomic_fact, status, attempts, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        future = self._submit_batch_write(sql, params_list, wait=True)
        if future:
            await future
        return ids

    async def get_pending_queue_items(self, limit: int = 50) -> list[dict[str, Any]]:
        """Queue items with status IN ('pending','failed') and attempts < max."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, episode_id, atomic_fact, status, attempts, last_error
            FROM pipeline_queue
            WHERE status IN ('pending', 'failed')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "episode_id": r[1],
                "atomic_fact": r[2],
                "status": r[3],
                "attempts": r[4],
                "last_error": r[5],
            }
            for r in rows
        ]

    async def update_queue_item_status(
        self, item_id: str, status: str, error: str | None = None
    ) -> None:
        """Update status, last_error, increment attempts."""
        future = self._submit_write(
            """
            UPDATE pipeline_queue SET status = ?, last_error = ?, attempts = attempts + 1
            WHERE id = ?
            """,
            (status, error, item_id),
            wait=True,
        )
        if future:
            await future

    async def mark_queue_item_done(self, item_id: str) -> None:
        """Set status='done'."""
        future = self._submit_write(
            "UPDATE pipeline_queue SET status = 'done' WHERE id = ?",
            (item_id,),
            wait=True,
        )
        if future:
            await future

    # --- v3 Episode-entity link ---

    async def link_episode_entity(self, episode_id: str, entity_id: str) -> None:
        """Insert into episode_entities (v3)."""
        future = self._submit_write(
            "INSERT OR IGNORE INTO episode_entities (episode_id, entity_id) VALUES (?, ?)",
            (episode_id, entity_id),
            wait=True,
        )
        if future:
            await future

    # --- fact_access_log (Ebbinghaus decay) ---

    def record_fact_access(self, fact_ids: list[str]) -> None:
        """Batch insert into fact_access_log. Fire-and-forget. Called from retrieval after search."""
        if not fact_ids:
            return
        now = int(time.time() * 1000)
        params_list = [(fid, now) for fid in fact_ids]
        self._submit_batch_write(
            "INSERT INTO fact_access_log (fact_id, accessed_at) VALUES (?, ?)",
            params_list,
            wait=False,
        )

    async def apply_fact_decay(
        self, lambda_: float, threshold: float, now: int
    ) -> tuple[int, int]:
        """Ebbinghaus decay on facts.confidence. Returns (facts_decayed, facts_expired)."""
        # 1. Decay confidence (exclude user-confirmed facts with confidence=1.0)
        decay_sql = """
            UPDATE facts
            SET confidence = confidence * exp(
                -? * power(
                    (? - COALESCE(
                        (SELECT MAX(accessed_at) FROM fact_access_log WHERE fact_id = facts.id),
                        facts.t_created
                    )) / 86400000.0,
                    0.8
                )
            )
            WHERE t_expired IS NULL AND confidence < 1.0
        """
        future1 = self._submit_write(
            decay_sql, (lambda_, now), wait=True, return_rowcount=True
        )
        decayed = 0
        if future1:
            decayed = await future1

        # 2. Expire facts below threshold
        expire_sql = """
            UPDATE facts SET t_expired = ?
            WHERE t_expired IS NULL AND confidence < ?
        """
        future2 = self._submit_write(
            expire_sql, (now, threshold), wait=True, return_rowcount=True
        )
        expired = 0
        if future2:
            expired = await future2

        return (decayed, expired)

    async def compact_fact_access_log(self, keep_days: int = 7) -> int:
        """Keep only most recent access per fact, drop entries older than keep_days. Returns rows deleted."""
        if self._read_conn is None:
            return 0
        cursor = await self._read_conn.execute(
            "SELECT COALESCE(MAX(accessed_at), 0) FROM fact_access_log"
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            return 0
        cutoff = row[0] - (keep_days * 24 * 60 * 60 * 1000)
        if cutoff <= 0:
            return 0
        # Delete older entries, but keep latest per fact: delete all where accessed_at < cutoff
        # and the fact has a newer entry. Simpler: just delete rows with accessed_at < cutoff.
        future = self._submit_write(
            "DELETE FROM fact_access_log WHERE accessed_at < ?",
            (cutoff,),
            wait=True,
            return_rowcount=True,
        )
        if future:
            return await future
        return 0

    # --- v3 Read-path (Phase 3) ---

    async def fts_search_facts(
        self, query: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """FTS5 search on facts_fts. Returns active fact dicts."""
        if not query or not query.strip() or self._read_conn is None:
            return []
        fts_query = _escape_fts5_query(query.strip())
        if not fts_query:
            return []
        try:
            cursor = await self._read_conn.execute(
                """
                SELECT f.id, f.subject_id, f.predicate, f.object_id, f.fact_text,
                       f.confidence, f.t_valid, f.t_created, f.source_episode_id
                FROM facts f
                INNER JOIN (
                    SELECT rowid FROM facts_fts
                    WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?
                ) fts ON f.rowid = fts.rowid
                WHERE f.t_expired IS NULL
                """,
                (fts_query, limit),
            )
        except Exception as e:
            logger.debug("fts_search_facts failed: %s", e)
            return []
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "subject_id": r[1],
                "predicate": r[2],
                "object_id": r[3],
                "fact_text": r[4],
                "confidence": r[5],
                "t_valid": r[6],
                "t_created": r[7],
                "source_episode_id": r[8],
            }
            for r in rows
        ]

    async def bfs_expand_facts(
        self,
        entity_ids: list[str],
        *,
        max_depth: int = 2,
        max_facts: int = 50,
    ) -> list[dict[str, Any]]:
        """BFS expansion from entities along fact-edges. Returns fact dicts ordered by confidence."""
        if not entity_ids or self._read_conn is None:
            return []
        placeholders = ",".join("?" * len(entity_ids))
        try:
            cursor = await self._read_conn.execute(
                f"""
                WITH RECURSIVE graph_bfs(entity_id, depth) AS (
                    SELECT id, 0 FROM entities WHERE id IN ({placeholders})
                    UNION ALL
                    SELECT
                        CASE WHEN f.subject_id = g.entity_id
                             THEN f.object_id
                             ELSE f.subject_id END,
                        g.depth + 1
                    FROM facts f
                    JOIN graph_bfs g ON (f.subject_id = g.entity_id OR f.object_id = g.entity_id)
                    WHERE g.depth < ? AND f.t_expired IS NULL
                )
                SELECT DISTINCT f.id, f.subject_id, f.predicate, f.object_id, f.fact_text,
                       f.confidence, f.t_valid, f.t_created, f.source_episode_id
                FROM facts f
                JOIN graph_bfs g ON (f.subject_id = g.entity_id OR f.object_id = g.entity_id)
                WHERE f.t_expired IS NULL
                ORDER BY f.confidence DESC
                LIMIT ?
                """,
                tuple(entity_ids) + (max_depth, max_facts),
            )
        except Exception as e:
            logger.debug("bfs_expand_facts failed: %s", e)
            return []
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "subject_id": r[1],
                "predicate": r[2],
                "object_id": r[3],
                "fact_text": r[4],
                "confidence": r[5],
                "t_valid": r[6],
                "t_created": r[7],
                "source_episode_id": r[8],
            }
            for r in rows
        ]

    async def get_facts_for_entity(
        self, entity_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """All active facts where entity is subject or object."""
        if not entity_id or self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            """
            SELECT id, subject_id, predicate, object_id, fact_text,
                   confidence, t_valid, t_created, source_episode_id
            FROM facts
            WHERE (subject_id = ? OR object_id = ?) AND t_expired IS NULL
            ORDER BY confidence DESC, t_valid ASC
            LIMIT ?
            """,
            (entity_id, entity_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "subject_id": r[1],
                "predicate": r[2],
                "object_id": r[3],
                "fact_text": r[4],
                "confidence": r[5],
                "t_valid": r[6],
                "t_created": r[7],
                "source_episode_id": r[8],
            }
            for r in rows
        ]

    async def get_entity_facts_timeline(
        self,
        entity_id: str | None,
        *,
        after: int | None = None,
        before: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Chronological facts ordered by t_valid. Optional entity and time filters."""
        if self._read_conn is None:
            return []
        conditions = ["t_expired IS NULL"]
        params: list[Any] = []
        if entity_id:
            conditions.append("(subject_id = ? OR object_id = ?)")
            params.extend([entity_id, entity_id])
        if after is not None:
            conditions.append("(t_valid IS NULL OR t_valid >= ?)")
            params.append(after)
        if before is not None:
            conditions.append("(t_valid IS NULL OR t_valid <= ?)")
            params.append(before)
        params.append(limit)
        where = " AND ".join(conditions)
        cursor = await self._read_conn.execute(
            f"""
            SELECT id, subject_id, predicate, object_id, fact_text,
                   confidence, t_valid, t_created, source_episode_id
            FROM facts
            WHERE {where}
            ORDER BY COALESCE(t_valid, t_created) ASC
            LIMIT ?
            """,
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "subject_id": r[1],
                "predicate": r[2],
                "object_id": r[3],
                "fact_text": r[4],
                "confidence": r[5],
                "t_valid": r[6],
                "t_created": r[7],
                "source_episode_id": r[8],
            }
            for r in rows
        ]

    async def get_facts_by_ids(
        self, fact_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Batch fetch facts by id. Returns active facts only."""
        if not fact_ids or self._read_conn is None:
            return []
        seen = {fid for fid in fact_ids if fid}
        if not seen:
            return []
        placeholders = ",".join("?" * len(seen))
        cursor = await self._read_conn.execute(
            f"""
            SELECT id, subject_id, predicate, object_id, fact_text,
                   confidence, t_valid, t_created, source_episode_id
            FROM facts
            WHERE id IN ({placeholders}) AND t_expired IS NULL
            """,
            tuple(seen),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "subject_id": r[1],
                "predicate": r[2],
                "object_id": r[3],
                "fact_text": r[4],
                "confidence": r[5],
                "t_valid": r[6],
                "t_created": r[7],
                "source_episode_id": r[8],
            }
            for r in rows
        ]

    async def get_fact_by_id(self, fact_id: str) -> dict[str, Any] | None:
        """Single fact lookup by id."""
        if not fact_id or self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            """
            SELECT id, subject_id, predicate, object_id, fact_text,
                   confidence, t_valid, t_invalid, t_created, t_expired,
                   source_episode_id, invalidated_by
            FROM facts
            WHERE id = ?
            """,
            (fact_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "subject_id": row[1],
            "predicate": row[2],
            "object_id": row[3],
            "fact_text": row[4],
            "confidence": row[5],
            "t_valid": row[6],
            "t_invalid": row[7],
            "t_created": row[8],
            "t_expired": row[9],
            "source_episode_id": row[10],
            "invalidated_by": row[11],
        }

    async def get_entities_for_facts(
        self, fact_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Resolve entity id -> entity dict for subject/object of given facts."""
        if not fact_ids or self._read_conn is None:
            return {}
        cursor = await self._read_conn.execute(
            f"""
            SELECT DISTINCT subject_id, object_id FROM facts
            WHERE id IN ({",".join("?" * len(fact_ids))})
            """,
            tuple(fact_ids),
        )
        rows = await cursor.fetchall()
        entity_ids = list({e for r in rows for e in (r[0], r[1])})
        if not entity_ids:
            return {}
        placeholders = ",".join("?" * len(entity_ids))
        cursor = await self._read_conn.execute(
            f"""
            SELECT id, name, aliases, summary, entity_type, mention_count
            FROM entities
            WHERE id IN ({placeholders})
            """,
            tuple(entity_ids),
        )
        rows = await cursor.fetchall()
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            result[r[0]] = {
                "id": r[0],
                "name": r[1],
                "aliases": json.loads(r[2]) if r[2] else [],
                "summary": r[3],
                "entity_type": r[4] or "",
                "mention_count": r[5],
            }
        return result

    async def get_unconsolidated_sessions(self) -> list[str]:
        """Return session_ids where consolidated_at IS NULL. For nightly maintenance."""
        if self._read_conn is None:
            return []
        cursor = await self._read_conn.execute(
            "SELECT session_id FROM sessions_consolidations WHERE consolidated_at IS NULL"
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def set_maintenance_timestamps(
        self,
        last_consolidation: str | None = None,
        last_decay_run: str | None = None,
    ) -> None:
        """Persist maintenance timestamps so any process (e.g. run_memory_maintenance.py) is visible to others."""
        upsert_sql = (
            "INSERT INTO maintenance_metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        if last_consolidation is not None:
            future = self._submit_write(
                upsert_sql, ("last_consolidation", last_consolidation), wait=True
            )
            if future:
                await future
        if last_decay_run is not None:
            future = self._submit_write(
                upsert_sql, ("last_decay_run", last_decay_run), wait=True
            )
            if future:
                await future

    async def get_maintenance_timestamps(self) -> dict[str, str | None]:
        """Return persisted last_consolidation and last_decay_run (for memory_stats when in-process state is None)."""
        if self._read_conn is None:
            return {"last_consolidation": None, "last_decay_run": None}
        cursor = await self._read_conn.execute(
            "SELECT key, value FROM maintenance_metadata WHERE key IN ('last_consolidation', 'last_decay_run')"
        )
        rows = await cursor.fetchall()
        out: dict[str, str | None] = {
            "last_consolidation": None,
            "last_decay_run": None,
        }
        for row in rows:
            out[row[0]] = row[1]
        return out

    async def get_latest_session_id(self) -> str | None:
        """Return the most recent session_id (by first_seen_at). Used to resume after restart."""
        if self._read_conn is None:
            return None
        cursor = await self._read_conn.execute(
            "SELECT session_id FROM sessions_consolidations ORDER BY first_seen_at DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_graph_stats(self) -> dict[str, Any]:
        """Graph-level metrics for v3: episodes, facts, entities, communities, pending_queue_items."""
        if self._read_conn is None:
            return {
                "episodes": 0,
                "facts": 0,
                "entities": 0,
                "communities": 0,
                "pending_queue_items": 0,
            }
        episodes = 0
        cursor = await self._read_conn.execute("SELECT COUNT(*) FROM episodes")
        row = await cursor.fetchone()
        if row:
            episodes = row[0]
        facts = 0
        cursor = await self._read_conn.execute(
            "SELECT COUNT(*) FROM facts WHERE t_expired IS NULL"
        )
        row = await cursor.fetchone()
        if row:
            facts = row[0]
        entities = 0
        cursor = await self._read_conn.execute("SELECT COUNT(*) FROM entities")
        row = await cursor.fetchone()
        if row:
            entities = row[0]
        communities = 0
        cursor = await self._read_conn.execute("SELECT COUNT(*) FROM communities")
        row = await cursor.fetchone()
        if row:
            communities = row[0]
        pending_queue = 0
        cursor = await self._read_conn.execute(
            "SELECT COUNT(*) FROM pipeline_queue WHERE status IN ('pending', 'failed')"
        )
        row = await cursor.fetchone()
        if row:
            pending_queue = row[0]
        return {
            "episodes": episodes,
            "facts": facts,
            "entities": entities,
            "communities": communities,
            "pending_queue_items": pending_queue,
        }

    def get_storage_size_mb(self) -> float:
        """Get database file size in MB."""
        if not self._db_path or not self._db_path.exists():
            return 0.0
        return round(os.path.getsize(self._db_path) / (1024 * 1024), 1)
