"""MemoryRepository: CRUD and FTS5 search. No business logic."""

import json
import re
import time
import uuid
from typing import Any

from db import MemoryDatabase  # noqa: I001 - db loaded from ext dir via sys.path


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

    async def soft_delete(self, memory_id: str) -> bool:
        """Set valid_until=now. Returns True if row existed."""
        conn = await self._db._ensure_conn()
        now = int(time.time())
        cursor = await conn.execute(
            "UPDATE memories SET valid_until = ? WHERE id = ? AND valid_until IS NULL",
            (now, memory_id),
        )
        await conn.commit()
        return cursor.rowcount is not None and cursor.rowcount > 0

    async def update_confidence(
        self, memory_id: str, confidence: float, decay_rate: float
    ) -> bool:
        """Update confidence and decay_rate (e.g. for confirm_fact)."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            "UPDATE memories SET confidence = ?, decay_rate = ? WHERE id = ? AND valid_until IS NULL",
            (confidence, decay_rate, memory_id),
        )
        await conn.commit()
        return cursor.rowcount is not None and cursor.rowcount > 0

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

    async def mark_session_consolidated(self, session_id: str) -> None:
        conn = await self._db._ensure_conn()
        await conn.execute(
            """UPDATE sessions_consolidations SET consolidated_at = ?
               WHERE session_id = ?""",
            (int(time.time()), session_id),
        )
        await conn.commit()

    async def get_episodes_by_session(self, session_id: str) -> list[dict[str, Any]]:
        """Fetch all episodes for a session (for consolidation)."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT id, content, source_role FROM memories
               WHERE session_id = ? AND kind = 'episode'
                 AND valid_until IS NULL
               ORDER BY created_at""",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [{"id": r[0], "content": r[1], "source_role": r[2]} for r in rows]


def _escape_fts5_query(q: str) -> str:
    """Sanitize for FTS5: keep only word chars and spaces. Prevents query syntax errors."""
    q = re.sub(r"[^\w\s]", " ", q, flags=re.UNICODE)
    return " ".join(w for w in q.split() if w)
