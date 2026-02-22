"""DedupService: batch fact save with two-level deduplication."""

from typing import Any

from db import MemoryDatabase  # noqa: I001 - db loaded from ext dir via sys.path
from search import MemorySearchService
from search_filter import SearchFilter

# If fact count exceeds this, fall back to per-fact FTS for Level 2 dedup (memory footprint).
_DEDUP_PREFETCH_LIMIT = 20_000


def _jaccard(a: str, b: str) -> float:
    """Jaccard similarity on word sets. Used for Level 2 dedup (5+ words only)."""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


class DedupService:
    """Save facts with two-level deduplication (intra-batch + against existing)."""

    def __init__(
        self,
        db: MemoryDatabase,
        crud: Any,  # MemoryCrudRepository
        search: MemorySearchService,
    ) -> None:
        self._db = db
        self._crud = crud
        self._search = search

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

        fact_filter = SearchFilter(kind="fact")
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
                existing = await self._search.fts_search(
                    content, sf=fact_filter, limit=1
                )
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
                memory_id = await self._crud.save_fact_with_sources(
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
