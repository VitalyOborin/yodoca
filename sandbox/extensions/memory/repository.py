"""MemoryRepository: CRUD, FTS5 search, vector search, hybrid RRF. No business logic."""

import json
import logging
import math
import re
import time
import uuid
from typing import Any

import sqlite_vec
from db import MemoryDatabase  # noqa: I001 - db loaded from ext dir via sys.path

logger = logging.getLogger(__name__)

# If fact count exceeds this, fall back to per-fact FTS for Level 2 dedup (memory footprint).
_DEDUP_PREFETCH_LIMIT = 20_000


class MemoryRepository:
    """Database operations for memories. FTS5 sync is handled by DB triggers."""

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

    async def fts_search(
        self,
        query: str,
        kind: str | None = None,
        tag: str | None = None,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """FTS5 search. Returns list of memory dicts. Empty query returns []."""
        if not query or not query.strip():
            return []
        conn = await self._db._ensure_conn()
        fts_query = _escape_fts5_query(query.strip())
        params: list[Any] = [fts_query, limit]
        kind_filter = " AND m.kind = ?" if kind else ""
        tag_filter = " AND m.tags LIKE ?" if tag else ""
        session_filter = ""
        if kind:
            params.append(kind)
        if tag:
            params.append(f'%"{tag}"%')
        if exclude_session_id:
            session_filter = " AND (m.session_id IS NULL OR m.session_id != ?)"
            params.append(exclude_session_id)
        sql = f"""
            SELECT m.id, m.kind, m.content, m.event_time, m.created_at,
                   m.confidence, m.tags
            FROM memories m
            INNER JOIN (
                SELECT rowid FROM memories_fts
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            ) f ON m.rowid = f.rowid
            WHERE m.valid_until IS NULL
            {kind_filter}
            {tag_filter}
            {session_filter}
        """
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "content": r[2],
                "event_time": r[3],
                "created_at": r[4],
                "confidence": r[5],
                "tags": json.loads(r[6]) if r[6] else [],
            }
            for r in rows
        ]

    async def save_embedding(self, memory_id: str, embedding: list[float]) -> None:
        """Store embedding in vec_memories. Overwrites if memory_id exists."""
        conn = await self._db._ensure_conn()
        blob = sqlite_vec.serialize_float32(embedding)
        await conn.execute(
            "INSERT OR REPLACE INTO vec_memories (memory_id, embedding) VALUES (?, ?)",
            (memory_id, blob),
        )
        await conn.commit()

    async def vector_search(
        self,
        query_embedding: list[float],
        kind: str | None = None,
        tag: str | None = None,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vector KNN search via vec0 MATCH. Returns same shape as fts_search (with distance)."""
        conn = await self._db._ensure_conn()
        blob = sqlite_vec.serialize_float32(query_embedding)
        buffer = limit * 3  # request extra to compensate for post-filter
        params: list[Any] = [blob, buffer]
        kind_filter = " AND m.kind = ?" if kind else ""
        tag_filter = " AND m.tags LIKE ?" if tag else ""
        session_filter = ""
        if kind:
            params.append(kind)
        if tag:
            params.append(f'%"{tag}"%')
        if exclude_session_id:
            session_filter = " AND (m.session_id IS NULL OR m.session_id != ?)"
            params.append(exclude_session_id)
        sql = f"""
            SELECT m.id, m.kind, m.content, m.event_time, m.created_at,
                   m.confidence, m.tags, v.distance
            FROM vec_memories v
            INNER JOIN memories m ON m.id = v.memory_id
            WHERE v.embedding MATCH ? AND v.k = ?
              AND m.valid_until IS NULL
            {kind_filter}
            {tag_filter}
            {session_filter}
            ORDER BY v.distance
            LIMIT ?
        """
        params.append(limit)
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "content": r[2],
                "event_time": r[3],
                "created_at": r[4],
                "confidence": r[5],
                "tags": json.loads(r[6]) if r[6] else [],
                "distance": r[7],
            }
            for r in rows
        ]

    def _rrf_merge(
        self,
        fts_results: list[dict[str, Any]],
        vec_results: list[dict[str, Any]],
        entity_results: list[dict[str, Any]] | None = None,
        k: int = 60,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Reciprocal Rank Fusion: score = sum(1/(k+rank)). Returns top limit by fused score."""
        scores: dict[str, float] = {}
        all_items: dict[str, dict[str, Any]] = {}
        for rank, item in enumerate(fts_results, start=1):
            mid = item["id"]
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)
            all_items[mid] = {k: v for k, v in item.items() if k != "distance"}
        for rank, item in enumerate(vec_results, start=1):
            mid = item["id"]
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)
            if mid not in all_items:
                all_items[mid] = {k: v for k, v in item.items() if k != "distance"}
        if entity_results:
            for rank, item in enumerate(entity_results, start=1):
                mid = item["id"]
                scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)
                if mid not in all_items:
                    all_items[mid] = {k: v for k, v in item.items() if k != "distance"}
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [all_items[mid] for mid, _ in ranked]

    async def hybrid_search(
        self,
        query: str,
        query_embedding: list[float] | None = None,
        kind: str | None = None,
        tag: str | None = None,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid FTS5 + vector + entity with RRF. If query_embedding is None, FTS5 + entity."""
        fts_results = await self.fts_search(
            query, kind=kind, tag=tag, limit=limit * 2, exclude_session_id=exclude_session_id
        )
        entity_results = await self.entity_search_for_rrf(
            query,
            kind=kind,
            tag=tag,
            limit=limit * 2,
            exclude_session_id=exclude_session_id,
        )
        if not query_embedding:
            return self._rrf_merge(
                fts_results, [], entity_results=entity_results, k=60, limit=limit
            )
        vec_results = await self.vector_search(
            query_embedding,
            kind=kind,
            tag=tag,
            limit=limit * 2,
            exclude_session_id=exclude_session_id,
        )
        return self._rrf_merge(
            fts_results, vec_results, entity_results=entity_results, k=60, limit=limit
        )

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
        """Fetch active memories created after since_ts with kind IN kinds.
        Returns list of dicts: id, kind, content, created_at, confidence, tags.
        Ordered by created_at DESC.
        """
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

    # --- Entity CRUD ---

    async def create_or_get_entity(
        self,
        canonical_name: str,
        entity_type: str,
        aliases: list[str] | None = None,
    ) -> str:
        """Create entity or return existing ID. Increment mention_count on match, merge aliases."""
        conn = await self._db._ensure_conn()
        canonical_lower = canonical_name.lower().strip()
        if not canonical_lower:
            raise ValueError("canonical_name cannot be empty")

        # Lookup by canonical_name + type
        cursor = await conn.execute(
            "SELECT id FROM entities WHERE LOWER(canonical_name) = ? AND type = ?",
            (canonical_lower, entity_type),
        )
        row = await cursor.fetchone()
        if row:
            entity_id = row[0]
            await conn.execute(
                "UPDATE entities SET mention_count = mention_count + 1 WHERE id = ?",
                (entity_id,),
            )
            if aliases:
                await self._merge_aliases(conn, entity_id, aliases)
            await conn.commit()
            return entity_id

        # Lookup by alias match (aliases JSON contains quoted strings)
        alias_pattern = f'%"{canonical_lower}"%'
        cursor = await conn.execute(
            """SELECT id FROM entities
               WHERE type = ? AND (LOWER(aliases) LIKE ? OR LOWER(canonical_name) LIKE ?)
               LIMIT 1""",
            (entity_type, alias_pattern, f"%{canonical_lower}%"),
        )
        row = await cursor.fetchone()
        if row:
            entity_id = row[0]
            await conn.execute(
                "UPDATE entities SET mention_count = mention_count + 1 WHERE id = ?",
                (entity_id,),
            )
            if aliases:
                await self._merge_aliases(conn, entity_id, aliases)
            await conn.commit()
            return entity_id

        # Create new entity
        entity_id = f"ent_{uuid.uuid4().hex[:12]}"
        aliases_json = json.dumps(aliases or [])
        await conn.execute(
            """INSERT INTO entities (id, canonical_name, type, aliases, mention_count)
               VALUES (?, ?, ?, ?, 1)""",
            (entity_id, canonical_name.strip(), entity_type, aliases_json),
        )
        await conn.commit()
        return entity_id

    async def _merge_aliases(
        self, conn: Any, entity_id: str, new_aliases: list[str]
    ) -> None:
        """Add new aliases to entity (no duplicates)."""
        cursor = await conn.execute(
            "SELECT aliases FROM entities WHERE id = ?", (entity_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return
        existing = set(json.loads(row[0]) if row[0] else [])
        merged = list(existing | set(a.strip() for a in new_aliases if a.strip()))
        await conn.execute(
            "UPDATE entities SET aliases = ? WHERE id = ?",
            (json.dumps(merged), entity_id),
        )

    async def link_memory_to_entities(
        self, memory_id: str, entity_ids: list[str]
    ) -> None:
        """Create memory_entities links. Idempotent (INSERT OR IGNORE)."""
        if not entity_ids:
            return
        conn = await self._db._ensure_conn()
        for entity_id in entity_ids:
            await conn.execute(
                """INSERT OR IGNORE INTO memory_entities (memory_id, entity_id)
                   VALUES (?, ?)""",
                (memory_id, entity_id),
            )
        await conn.commit()

    async def search_entities(
        self,
        query: str,
        entity_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search entities by canonical_name or aliases (case-insensitive partial match)."""
        if not query or not query.strip():
            return []
        conn = await self._db._ensure_conn()
        query_lower = f"%{query.lower().strip()}%"
        type_filter = " AND type = ?" if entity_type else ""
        params: list[Any] = [query_lower, query_lower]
        if entity_type:
            params.append(entity_type)
        params.append(limit)
        cursor = await conn.execute(
            f"""
            SELECT id, canonical_name, type, aliases, mention_count
            FROM entities
            WHERE (LOWER(canonical_name) LIKE ? OR LOWER(aliases) LIKE ?)
            {type_filter}
            ORDER BY mention_count DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "canonical_name": r[1],
                "type": r[2],
                "aliases": json.loads(r[3]) if r[3] else [],
                "mention_count": r[4],
            }
            for r in rows
        ]

    async def get_memories_by_entity(
        self,
        entity_id: str,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get memories linked to entity via junction table."""
        conn = await self._db._ensure_conn()
        kind_filter = " AND m.kind = ?" if kind else ""
        params: list[Any] = [entity_id]
        if kind:
            params.append(kind)
        params.append(limit)
        cursor = await conn.execute(
            f"""
            SELECT m.id, m.kind, m.content, m.created_at, m.confidence
            FROM memories m
            INNER JOIN memory_entities me ON me.memory_id = m.id
            WHERE me.entity_id = ?
              AND m.valid_until IS NULL
            {kind_filter}
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "content": r[2],
                "created_at": r[3],
                "confidence": r[4],
            }
            for r in rows
        ]

    async def entity_search_for_rrf(
        self,
        query: str,
        kind: str | None = None,
        tag: str | None = None,
        limit: int = 10,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Entity-based memory search for RRF. Returns same dict shape as fts_search."""
        if not query or not query.strip():
            return []
        conn = await self._db._ensure_conn()
        query_lower = f"%{query.lower().strip()}%"
        kind_filter = " AND m.kind = ?" if kind else ""
        tag_filter = " AND m.tags LIKE ?" if tag else ""
        session_filter = ""
        params: list[Any] = [query_lower, query_lower]
        if kind:
            params.append(kind)
        if tag:
            params.append(f'%"{tag}"%')
        if exclude_session_id:
            session_filter = " AND (m.session_id IS NULL OR m.session_id != ?)"
            params.append(exclude_session_id)
        params.append(limit)
        cursor = await conn.execute(
            f"""
            SELECT m.id, m.kind, m.content, m.event_time, m.created_at,
                   m.confidence, m.tags
            FROM memories m
            INNER JOIN memory_entities me ON me.memory_id = m.id
            INNER JOIN entities e ON e.id = me.entity_id
            WHERE m.valid_until IS NULL
              AND (LOWER(e.canonical_name) LIKE ? OR LOWER(e.aliases) LIKE ?)
            {kind_filter}
            {tag_filter}
            {session_filter}
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "kind": r[1],
                "content": r[2],
                "event_time": r[3],
                "created_at": r[4],
                "confidence": r[5],
                "tags": json.loads(r[6]) if r[6] else [],
            }
            for r in rows
        ]

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

    async def get_facts_for_decay(self) -> list[dict[str, Any]]:
        """Return active facts with decay_rate > 0, sorted oldest first."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT id, confidence, decay_rate, last_accessed, created_at
               FROM memories
               WHERE kind = 'fact'
                 AND valid_until IS NULL
                 AND decay_rate > 0
               ORDER BY COALESCE(last_accessed, created_at) ASC, created_at ASC""",
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "confidence": r[1],
                "decay_rate": r[2],
                "last_accessed": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]

    async def apply_decay_and_prune(
        self, threshold: float = 0.05
    ) -> dict[str, Any]:
        """
        Apply Ebbinghaus decay: confidence *= exp(-decay_rate * days_since_access).
        Soft-delete facts where new_confidence < threshold.
        Returns stats: {decayed, pruned, errors}.
        """
        facts = await self.get_facts_for_decay()
        now = int(time.time())
        decayed = 0
        pruned = 0
        errors: list[str] = []
        conn = await self._db._ensure_conn()

        for fact in facts:
            try:
                ref_ts = fact["last_accessed"] or fact["created_at"]
                days = max(0.0, (now - ref_ts) / 86400.0)
                new_conf = fact["confidence"] * math.exp(
                    -fact["decay_rate"] * days
                )
                new_conf = max(0.0, min(1.0, new_conf))

                if new_conf < threshold:
                    await conn.execute(
                        "UPDATE memories SET valid_until = ? WHERE id = ?",
                        (now, fact["id"]),
                    )
                    await conn.execute(
                        "DELETE FROM vec_memories WHERE memory_id = ?",
                        (fact["id"],),
                    )
                    await conn.execute(
                        "DELETE FROM memory_entities WHERE memory_id = ?",
                        (fact["id"],),
                    )
                    pruned += 1
                else:
                    await conn.execute(
                        """UPDATE memories
                           SET confidence = ?, last_accessed = ?
                           WHERE id = ?""",
                        (new_conf, now, fact["id"]),
                    )
                    decayed += 1
            except Exception as e:
                errors.append(f"fact {fact['id']}: {e}")
                logger.exception("Decay error for fact %s", fact["id"])

        await conn.commit()
        return {"decayed": decayed, "pruned": pruned, "errors": errors}

    async def _get_existing_facts_for_dedup(
        self, kind: str = "fact"
    ) -> list[dict[str, str]]:
        """Fetch id + content of all active facts for batch dedup. Returns lightweight dicts."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT id, content FROM memories
               WHERE kind = ? AND valid_until IS NULL""",
            (kind,),
        )
        rows = await cursor.fetchall()
        return [{"id": r[0], "content": r[1] or ""} for r in rows]

    @staticmethod
    def _is_duplicate_of_existing(
        content: str,
        existing: list[dict[str, str]],
        threshold: float = 0.75,
    ) -> bool:
        """Check if content is a Jaccard duplicate of any existing fact."""
        wa = set(content.lower().split())
        if len(wa) < 5:
            return False
        for item in existing:
            wb = set((item.get("content") or "").lower().split())
            if len(wb) >= 5 and _jaccard(content, item["content"]) > threshold:
                return True
        return False

    async def save_facts_batch(
        self, session_id: str, facts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Save facts with two-level deduplication. Returns saved, skipped_duplicates, errors."""
        result: dict[str, Any] = {
            "saved": [],
            "skipped_duplicates": 0,
            "errors": [],
        }
        if not facts:
            return result

        # Level 2: pre-fetch existing facts for batch dedup, or fall back to per-fact FTS
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT COUNT(*) FROM memories
               WHERE kind = 'fact' AND valid_until IS NULL"""
        )
        row = await cursor.fetchone()
        fact_count = int(row[0]) if row and row[0] is not None else 0
        use_prefetch = fact_count <= _DEDUP_PREFETCH_LIMIT
        existing_facts: list[dict[str, str]] = []
        if use_prefetch:
            existing_facts = await self._get_existing_facts_for_dedup(kind="fact")

        seen: set[str] = set()
        for fact in facts:
            content = (fact.get("content") or "").strip()
            if not content:
                continue
            normalized = content.lower().strip()
            # Level 1: intra-batch exact dupes
            if normalized in seen:
                result["skipped_duplicates"] += 1
                continue
            seen.add(normalized)

            # Level 2: against existing memory
            if use_prefetch:
                if self._is_duplicate_of_existing(content, existing_facts):
                    result["skipped_duplicates"] += 1
                    continue
            else:
                existing = await self.fts_search(content, kind="fact", limit=1)
                if existing:
                    wa = set(content.lower().split())
                    wb = set(existing[0]["content"].lower().split())
                    if (
                        len(wa) >= 5
                        and len(wb) >= 5
                        and _jaccard(content, existing[0]["content"]) > 0.75
                    ):
                        result["skipped_duplicates"] += 1
                        continue

            try:
                confidence = float(fact.get("confidence", 1.0))
                memory_id = await self.save_fact_with_sources(
                    content=content,
                    source_ids=fact.get("source_ids") or [],
                    session_id=session_id,
                    confidence=confidence,
                    tags=fact.get("tags"),
                )
                preview = f"{content[:80]}..." if len(content) > 80 else content
                result["saved"].append(
                    {
                        "id": memory_id,
                        "content_preview": preview,
                        "content": content,
                        "confidence": confidence,
                        "duplicate": False,
                    }
                )
            except Exception as e:
                result["errors"].append(f"{content[:50]}...: {e}")

        return result


def _escape_fts5_query(q: str) -> str:
    """Sanitize for FTS5: keep only word chars and spaces. Prevents query syntax errors."""
    q = re.sub(r"[^\w\s]", " ", q, flags=re.UNICODE)
    return " ".join(w for w in q.split() if w)


def _jaccard(a: str, b: str) -> float:
    """Jaccard similarity on word sets. Used for Level 2 dedup (5+ words only)."""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)
