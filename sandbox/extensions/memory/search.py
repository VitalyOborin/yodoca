"""MemorySearchService: FTS5, vector, entity search, hybrid RRF."""

import json
import re
from typing import Any

import sqlite_vec

from db import MemoryDatabase  # noqa: I001 - db loaded from ext dir via sys.path
from search_filter import SearchFilter


def _escape_fts5_query(q: str) -> str:
    """Sanitize for FTS5: keep only word chars and spaces. Prevents query syntax errors."""
    q = re.sub(r"[^\w\s]", " ", q, flags=re.UNICODE)
    return " ".join(w for w in q.split() if w)


class MemorySearchService:
    """FTS5, vector, and entity-based memory search with RRF fusion."""

    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    async def fts_search(
        self,
        query: str,
        sf: SearchFilter | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """FTS5 search. Returns list of memory dicts. Empty query returns []."""
        if not query or not query.strip():
            return []
        sf = sf or SearchFilter()
        conn = await self._db._ensure_conn()
        fts_query = _escape_fts5_query(query.strip())
        params: list[Any] = [fts_query, limit]
        filter_sql, filter_params = sf.build_clauses("m")
        params.extend(filter_params)
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
            {filter_sql}
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

    async def vector_search(
        self,
        query_embedding: list[float],
        sf: SearchFilter | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Vector KNN search via vec0 MATCH. Returns same shape as fts_search (with distance)."""
        sf = sf or SearchFilter()
        conn = await self._db._ensure_conn()
        blob = sqlite_vec.serialize_float32(query_embedding)
        buffer = limit * 3  # request extra to compensate for post-filter
        params: list[Any] = [blob, buffer]
        filter_sql, filter_params = sf.build_clauses("m")
        params.extend(filter_params)
        params.append(limit)
        sql = f"""
            SELECT m.id, m.kind, m.content, m.event_time, m.created_at,
                   m.confidence, m.tags, v.distance
            FROM vec_memories v
            INNER JOIN memories m ON m.id = v.memory_id
            WHERE v.embedding MATCH ? AND v.k = ?
              AND m.valid_until IS NULL
            {filter_sql}
            ORDER BY v.distance
            LIMIT ?
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
                "distance": r[7],
            }
            for r in rows
        ]

    async def entity_search_for_rrf(
        self,
        query: str,
        sf: SearchFilter | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Entity-based memory search for RRF. Returns same dict shape as fts_search."""
        if not query or not query.strip():
            return []
        sf = sf or SearchFilter()
        conn = await self._db._ensure_conn()
        query_lower = f"%{query.lower().strip()}%"
        params: list[Any] = [query_lower, query_lower]
        filter_sql, filter_params = sf.build_clauses("m")
        params.extend(filter_params)
        params.append(limit)
        sql = f"""
            SELECT m.id, m.kind, m.content, m.event_time, m.created_at,
                   m.confidence, m.tags
            FROM memories m
            INNER JOIN memory_entities me ON me.memory_id = m.id
            INNER JOIN entities e ON e.id = me.entity_id
            WHERE m.valid_until IS NULL
              AND (LOWER(e.canonical_name) LIKE ? OR LOWER(e.aliases) LIKE ?)
            {filter_sql}
            ORDER BY m.created_at DESC
            LIMIT ?
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
        sf: SearchFilter | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Hybrid FTS5 + vector + entity with RRF. If query_embedding is None, FTS5 + entity."""
        sf = sf or SearchFilter()
        fts_results = await self.fts_search(query, sf=sf, limit=limit * 2)
        entity_results = await self.entity_search_for_rrf(
            query, sf=sf, limit=limit * 2
        )
        if not query_embedding:
            return self._rrf_merge(
                fts_results, [], entity_results=entity_results, k=60, limit=limit
            )
        vec_results = await self.vector_search(
            query_embedding, sf=sf, limit=limit * 2
        )
        return self._rrf_merge(
            fts_results, vec_results, entity_results=entity_results, k=60, limit=limit
        )
