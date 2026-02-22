"""Memory CRUD + facade. MemoryRepository delegates to domain services."""

import json
import time
import uuid
from typing import Any

import sqlite_vec

from db import MemoryDatabase  # noqa: I001 - db loaded from ext dir via sys.path
from decay import DecayService
from dedup import DedupService
from entities import EntityRepository
from search import MemorySearchService
from search_filter import SearchFilter


class MemoryCrudRepository:
    """CRUD for memories, sessions, stats. No search, entities, decay, or dedup."""

    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    async def save_episode(
        self,
        content: str,
        session_id: str | None = None,
        source_role: str | None = None,
    ) -> str:
        """Insert episode; event_time=created_at. source_role: 'user' or agent name."""
        conn = await self._db._ensure_conn()
        now = int(time.time())
        memory_id = f"ep_{uuid.uuid4().hex[:12]}"
        role = (source_role or "")[:255] if source_role else None
        await conn.execute(
            """
            INSERT INTO memories (id, kind, content, session_id, event_time, created_at, source_role)
            VALUES (?, 'episode', ?, ?, ?, ?, ?)
            """,
            (memory_id, content, session_id, now, now, role),
        )
        await conn.commit()
        return memory_id

    async def save_fact(
        self,
        content: str,
        confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> str:
        """Insert fact. Returns new memory id."""
        conn = await self._db._ensure_conn()
        now = int(time.time())
        memory_id = f"fact_{uuid.uuid4().hex[:12]}"
        tags_json = json.dumps(tags or [])
        await conn.execute(
            """
            INSERT INTO memories (id, kind, content, event_time, created_at, confidence, tags)
            VALUES (?, 'fact', ?, ?, ?, ?, ?)
            """,
            (memory_id, content, now, now, confidence, tags_json),
        )
        await conn.commit()
        return memory_id

    async def save_fact_with_sources(
        self,
        content: str,
        source_ids: list[str],
        session_id: str | None = None,
        confidence: float = 1.0,
        tags: list[str] | None = None,
    ) -> str:
        """Insert fact with provenance. Returns new memory id."""
        conn = await self._db._ensure_conn()
        now = int(time.time())
        memory_id = f"fact_{uuid.uuid4().hex[:12]}"
        source_ids_json = json.dumps(source_ids)
        tags_json = json.dumps(tags or [])
        await conn.execute(
            """
            INSERT INTO memories (id, kind, content, session_id, event_time, created_at,
                                 confidence, tags, source_ids)
            VALUES (?, 'fact', ?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, content, session_id, now, now, confidence, tags_json, source_ids_json),
        )
        await conn.commit()
        return memory_id

    async def soft_delete(self, memory_id: str) -> bool:
        """Set valid_until=now. Returns True if row existed."""
        conn = await self._db._ensure_conn()
        now = int(time.time())
        cursor = await conn.execute(
            "UPDATE memories SET valid_until = ? WHERE id = ? AND valid_until IS NULL",
            (now, memory_id),
        )
        await conn.execute(
            "DELETE FROM vec_memories WHERE memory_id = ?",
            (memory_id,),
        )
        await conn.execute(
            "DELETE FROM memory_entities WHERE memory_id = ?",
            (memory_id,),
        )
        await conn.commit()
        return cursor.rowcount is not None and cursor.rowcount > 0

    async def update_confidence(
        self, memory_id: str, confidence: float, decay_rate: float
    ) -> bool:
        """Update confidence, decay_rate, and last_accessed (e.g. for confirm_fact)."""
        conn = await self._db._ensure_conn()
        now = int(time.time())
        cursor = await conn.execute(
            """UPDATE memories
               SET confidence = ?, decay_rate = ?, last_accessed = ?
               WHERE id = ? AND valid_until IS NULL""",
            (confidence, decay_rate, now, memory_id),
        )
        await conn.commit()
        return cursor.rowcount is not None and cursor.rowcount > 0

    async def update_attributes(self, memory_id: str, patch: dict[str, Any]) -> bool:
        """Merge patch into memory's attributes JSON. Returns True if row existed."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            "SELECT attributes FROM memories WHERE id = ? AND valid_until IS NULL",
            (memory_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        current = json.loads(row[0]) if row[0] else {}
        merged = {**current, **patch}
        await conn.execute(
            "UPDATE memories SET attributes = ? WHERE id = ?",
            (json.dumps(merged), memory_id),
        )
        await conn.commit()
        return True

    async def get_memory_session_id(self, memory_id: str) -> str | None:
        """Return session_id for a memory, or None if not found."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            "SELECT session_id FROM memories WHERE id = ? AND valid_until IS NULL",
            (memory_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None

    async def save_embedding(self, memory_id: str, embedding: list[float]) -> None:
        """Store embedding in vec_memories. Overwrites if memory_id exists."""
        conn = await self._db._ensure_conn()
        blob = sqlite_vec.serialize_float32(embedding)
        await conn.execute(
            "INSERT OR REPLACE INTO vec_memories (memory_id, embedding) VALUES (?, ?)",
            (memory_id, blob),
        )
        await conn.commit()

    async def get_stats(self) -> dict[str, Any]:
        """Counts by kind, latest created_at."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """
            SELECT kind, COUNT(*) FROM memories
            WHERE valid_until IS NULL
            GROUP BY kind
            """
        )
        rows = await cursor.fetchall()
        counts = {r[0]: r[1] for r in rows}
        cursor = await conn.execute(
            "SELECT MAX(created_at) FROM memories WHERE valid_until IS NULL"
        )
        row = await cursor.fetchone()
        latest = row[0] if row and row[0] else None
        return {"counts": counts, "latest_created_at": latest}

    async def get_recent_memories(
        self,
        since_ts: int,
        kinds: list[str],
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Fetch active memories created after since_ts with kind IN kinds."""
        if not kinds:
            return []
        conn = await self._db._ensure_conn()
        placeholders = ",".join("?" * len(kinds))
        params: list[Any] = [*kinds, since_ts, limit]
        sql = f"""
            SELECT id, kind, content, created_at, confidence, tags
            FROM memories
            WHERE kind IN ({placeholders})
              AND valid_until IS NULL
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT ?
        """
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "content": r[2] or "",
                "created_at": r[3],
                "confidence": float(r[4]) if r[4] is not None else 1.0,
                "tags": json.loads(r[5]) if r[5] else [],
            }
            for r in rows
        ]

    async def save_reflection(
        self,
        content: str,
        source_ids: list[str] | None = None,
        tags: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        """Insert reflection. Protected by default (decay_rate=0.0). Returns new memory id."""
        conn = await self._db._ensure_conn()
        now = int(time.time())
        memory_id = f"ref_{uuid.uuid4().hex[:12]}"
        source_ids_json = json.dumps(source_ids or [])
        tags_json = json.dumps(tags or [])
        attrs_json = json.dumps(attributes or {})
        await conn.execute(
            """
            INSERT INTO memories (id, kind, content, event_time, created_at,
                                 decay_rate, source_ids, tags, attributes)
            VALUES (?, 'reflection', ?, ?, ?, 0.0, ?, ?, ?)
            """,
            (memory_id, content, now, now, source_ids_json, tags_json, attrs_json),
        )
        await conn.commit()
        return memory_id

    async def get_latest_reflection(self) -> dict[str, Any] | None:
        """Return the most recent active reflection for context injection."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """
            SELECT id, content, created_at
            FROM memories
            WHERE kind = 'reflection' AND valid_until IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {"id": row[0], "content": row[1] or "", "created_at": row[2]}

    async def get_latest_reflection_timestamp(self) -> int | None:
        """Return created_at of the most recent reflection, or None if none exist."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """
            SELECT created_at FROM memories
            WHERE kind = 'reflection' AND valid_until IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None

    async def ensure_session(self, session_id: str) -> None:
        """Register session on first sight."""
        conn = await self._db._ensure_conn()
        await conn.execute(
            """INSERT OR IGNORE INTO sessions_consolidations (session_id, first_seen_at)
               VALUES (?, ?)""",
            (session_id, int(time.time())),
        )
        await conn.commit()

    async def get_pending_consolidations(
        self, exclude_session_id: str
    ) -> list[str]:
        """Return session_ids that need consolidation (all except current)."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT session_id FROM sessions_consolidations
               WHERE consolidated_at IS NULL
                 AND session_id != ?""",
            (exclude_session_id,),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def get_all_pending_consolidations(self) -> list[str]:
        """Return all session_ids that need consolidation (no exclusions)."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT session_id FROM sessions_consolidations
               WHERE consolidated_at IS NULL""",
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def is_session_consolidated(self, session_id: str) -> bool:
        """Check if session was already consolidated. Prevents duplicate runs."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT 1 FROM sessions_consolidations
               WHERE session_id = ? AND consolidated_at IS NOT NULL""",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def mark_session_consolidated(self, session_id: str) -> None:
        conn = await self._db._ensure_conn()
        await conn.execute(
            """UPDATE sessions_consolidations SET consolidated_at = ?
               WHERE session_id = ?""",
            (int(time.time()), session_id),
        )
        await conn.commit()

    async def count_facts_by_session(self, session_id: str) -> int:
        """Count facts saved for a session (for consolidation reporting)."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT COUNT(*) FROM memories
               WHERE session_id = ? AND kind = 'fact' AND valid_until IS NULL""",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else 0

    async def get_episodes_by_session(
        self, session_id: str, offset: int = 0, limit: int = 30
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch episodes for a session (paginated). Returns (page, total_count)."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT id, content, source_role, COUNT(*) OVER() AS total_count
               FROM memories
               WHERE session_id = ? AND kind = 'episode'
                 AND valid_until IS NULL
               ORDER BY created_at
               LIMIT ? OFFSET ?""",
            (session_id, limit, offset),
        )
        rows = await cursor.fetchall()
        total = int(rows[0][3]) if rows else 0
        page = [{"id": r[0], "content": r[1], "source_role": r[2]} for r in rows]
        return (page, total)


class MemoryRepository:
    """Facade: delegates to domain services. Preserves backward compat for main, tools, entity_linker."""

    def __init__(self, db: MemoryDatabase) -> None:
        self._crud = MemoryCrudRepository(db)
        self._search = MemorySearchService(db)
        self._entities = EntityRepository(db)
        self._decay = DecayService(db)
        self._dedup = DedupService(db, self._crud, self._search)

    def _sf(
        self,
        kind: str | None = None,
        tag: str | None = None,
        after_ts: int | None = None,
        before_ts: int | None = None,
        exclude_session_id: str | None = None,
    ) -> SearchFilter:
        return SearchFilter(
            kind=kind,
            tag=tag,
            after_ts=after_ts,
            before_ts=before_ts,
            exclude_session_id=exclude_session_id,
        )

    # --- Search (delegate to MemorySearchService) ---

    async def fts_search(
        self,
        query: str,
        kind: str | None = None,
        tag: str | None = None,
        after_ts: int | None = None,
        before_ts: int | None = None,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sf = self._sf(kind=kind, tag=tag, after_ts=after_ts, before_ts=before_ts, exclude_session_id=exclude_session_id)
        return await self._search.fts_search(query, sf=sf, limit=limit)

    async def vector_search(
        self,
        query_embedding: list[float],
        kind: str | None = None,
        tag: str | None = None,
        after_ts: int | None = None,
        before_ts: int | None = None,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sf = self._sf(kind=kind, tag=tag, after_ts=after_ts, before_ts=before_ts, exclude_session_id=exclude_session_id)
        return await self._search.vector_search(query_embedding, sf=sf, limit=limit)

    async def entity_search_for_rrf(
        self,
        query: str,
        kind: str | None = None,
        tag: str | None = None,
        after_ts: int | None = None,
        before_ts: int | None = None,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sf = self._sf(kind=kind, tag=tag, after_ts=after_ts, before_ts=before_ts, exclude_session_id=exclude_session_id)
        return await self._search.entity_search_for_rrf(query, sf=sf, limit=limit)

    async def hybrid_search(
        self,
        query: str,
        query_embedding: list[float] | None = None,
        kind: str | None = None,
        tag: str | None = None,
        after_ts: int | None = None,
        before_ts: int | None = None,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sf = self._sf(kind=kind, tag=tag, after_ts=after_ts, before_ts=before_ts, exclude_session_id=exclude_session_id)
        return await self._search.hybrid_search(query, query_embedding=query_embedding, sf=sf, limit=limit)

    # --- CRUD (delegate to MemoryCrudRepository) ---

    async def save_episode(self, *args: Any, **kwargs: Any) -> str:
        return await self._crud.save_episode(*args, **kwargs)

    async def save_fact(self, *args: Any, **kwargs: Any) -> str:
        return await self._crud.save_fact(*args, **kwargs)

    async def save_fact_with_sources(self, *args: Any, **kwargs: Any) -> str:
        return await self._crud.save_fact_with_sources(*args, **kwargs)

    async def soft_delete(self, memory_id: str) -> bool:
        return await self._crud.soft_delete(memory_id)

    async def update_confidence(
        self, memory_id: str, confidence: float, decay_rate: float
    ) -> bool:
        return await self._crud.update_confidence(memory_id, confidence, decay_rate)

    async def update_attributes(self, memory_id: str, patch: dict[str, Any]) -> bool:
        return await self._crud.update_attributes(memory_id, patch)

    async def get_memory_session_id(self, memory_id: str) -> str | None:
        return await self._crud.get_memory_session_id(memory_id)

    async def save_embedding(self, memory_id: str, embedding: list[float]) -> None:
        return await self._crud.save_embedding(memory_id, embedding)

    async def get_stats(self) -> dict[str, Any]:
        return await self._crud.get_stats()

    async def get_recent_memories(
        self, since_ts: int, kinds: list[str], limit: int = 200
    ) -> list[dict[str, Any]]:
        return await self._crud.get_recent_memories(since_ts, kinds, limit)

    async def save_reflection(
        self,
        content: str,
        source_ids: list[str] | None = None,
        tags: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        return await self._crud.save_reflection(
            content, source_ids=source_ids, tags=tags, attributes=attributes
        )

    async def get_latest_reflection(self) -> dict[str, Any] | None:
        return await self._crud.get_latest_reflection()

    async def get_latest_reflection_timestamp(self) -> int | None:
        return await self._crud.get_latest_reflection_timestamp()

    async def ensure_session(self, session_id: str) -> None:
        return await self._crud.ensure_session(session_id)

    async def get_pending_consolidations(self, exclude_session_id: str) -> list[str]:
        return await self._crud.get_pending_consolidations(exclude_session_id)

    async def get_all_pending_consolidations(self) -> list[str]:
        return await self._crud.get_all_pending_consolidations()

    async def is_session_consolidated(self, session_id: str) -> bool:
        return await self._crud.is_session_consolidated(session_id)

    async def mark_session_consolidated(self, session_id: str) -> None:
        return await self._crud.mark_session_consolidated(session_id)

    async def count_facts_by_session(self, session_id: str) -> int:
        return await self._crud.count_facts_by_session(session_id)

    async def get_episodes_by_session(
        self, session_id: str, offset: int = 0, limit: int = 30
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._crud.get_episodes_by_session(session_id, offset, limit)

    # --- Entities (delegate to EntityRepository) ---

    async def create_or_get_entity(
        self,
        canonical_name: str,
        entity_type: str,
        aliases: list[str] | None = None,
    ) -> str:
        return await self._entities.create_or_get_entity(
            canonical_name, entity_type, aliases
        )

    async def link_memory_to_entities(
        self, memory_id: str, entity_ids: list[str]
    ) -> None:
        return await self._entities.link_memory_to_entities(memory_id, entity_ids)

    async def get_memories_for_entity_enrichment(
        self,
        kinds: list[str] | None = None,
        max_entity_count: int = 2,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._entities.get_memories_for_entity_enrichment(
            kinds=kinds, max_entity_count=max_entity_count, limit=limit
        )

    async def mark_memory_enriched(self, memory_id: str) -> None:
        return await self._entities.mark_memory_enriched(memory_id)

    async def search_entities(
        self,
        query: str,
        entity_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        return await self._entities.search_entities(
            query, entity_type=entity_type, limit=limit
        )

    async def get_memories_by_entity(
        self,
        entity_id: str,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return await self._entities.get_memories_by_entity(
            entity_id, kind=kind, limit=limit
        )

    # --- Decay (delegate to DecayService) ---

    async def get_facts_for_decay(self) -> list[dict[str, Any]]:
        return await self._decay.get_facts_for_decay()

    async def apply_decay_and_prune(self, threshold: float = 0.05) -> dict[str, Any]:
        return await self._decay.apply_decay_and_prune(threshold)

    # --- Dedup (delegate to DedupService) ---

    async def save_facts_batch(
        self, session_id: str, facts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return await self._dedup.save_facts_batch(session_id, facts)
