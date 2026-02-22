"""EntityRepository: entity CRUD, linking, search, enrichment."""

import json
import time
import uuid
from typing import Any

from db import MemoryDatabase  # noqa: I001 - db loaded from ext dir via sys.path


class EntityRepository:
    """Entity CRUD, memory-entity linking, and entity-based memory queries."""

    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

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

    async def get_memories_for_entity_enrichment(
        self,
        kinds: list[str] | None = None,
        max_entity_count: int = 2,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch memories that need entity enrichment.

        Returns memories with enriched_at IS NULL and entity_count <= max_entity_count.
        Prioritizes recent memories. Used by nightly entity enrichment task.
        """
        if not kinds:
            return []
        conn = await self._db._ensure_conn()
        placeholders = ",".join("?" * len(kinds))
        params: list[Any] = [*kinds, max_entity_count, limit]
        sql = f"""
            SELECT m.id, m.kind, m.content, m.created_at,
                   COUNT(me.entity_id) as entity_count
            FROM memories m
            LEFT JOIN memory_entities me ON me.memory_id = m.id
            WHERE m.valid_until IS NULL
              AND m.kind IN ({placeholders})
              AND json_extract(m.attributes, '$.enriched_at') IS NULL
            GROUP BY m.id
            HAVING COUNT(me.entity_id) <= ?
            ORDER BY m.created_at DESC
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
                "entity_count": r[4],
            }
            for r in rows
        ]

    async def mark_memory_enriched(self, memory_id: str) -> None:
        """Stamp attributes.enriched_at to prevent re-processing."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            "SELECT attributes FROM memories WHERE id = ?", (memory_id,)
        )
        row = await cursor.fetchone()
        attrs = json.loads(row[0]) if row and row[0] else {}
        attrs["enriched_at"] = int(time.time())
        await conn.execute(
            "UPDATE memories SET attributes = ? WHERE id = ?",
            (json.dumps(attrs), memory_id),
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
