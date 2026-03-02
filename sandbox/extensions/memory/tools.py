"""Orchestrator tools for Memory v3. Phase 3: full tool set with HierarchicalRetriever."""

import asyncio
import logging
from typing import Any, Callable

from agents import function_tool
from pydantic import BaseModel, Field

from retrieval import HierarchicalRetriever, _resolve_entity, parse_time_expression

logger = logging.getLogger(__name__)


# --- Pydantic result models (v3 schema) ---


class SearchResult(BaseModel):
    """Result of search_memory. results are fact dicts."""

    results: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class RememberResult(BaseModel):
    """Result of remember_fact."""

    fact_id: str = ""
    status: str = "saved"


class CorrectResult(BaseModel):
    """Result of correct_fact."""

    old_fact_id: str = ""
    new_fact_id: str = ""
    status: str = "corrected"


class EntityInfoResult(BaseModel):
    """Result of get_entity_info. name instead of canonical_name."""

    entity_id: str = ""
    name: str = ""
    summary: str = ""
    facts: list[str] = Field(default_factory=list)
    status: str = "ok"


class MemoryStatsResult(BaseModel):
    """Result of memory_stats. v3 schema."""

    episodes: int = 0
    facts: int = 0
    entities: int = 0
    communities: int = 0
    pending_queue_items: int = 0
    unconsolidated_sessions: int = 0
    storage_size_mb: float = 0.0
    last_consolidation: str | None = None
    last_decay_run: str | None = None


class TimelineEvent(BaseModel):
    """Single timeline event."""

    id: str
    timestamp: str
    content: str


class TimelineResult(BaseModel):
    """Result of get_timeline."""

    events: list[TimelineEvent] = Field(default_factory=list)
    count: int = 0
    status: str = "ok"


class ForgetResult(BaseModel):
    """Result of forget_fact."""

    fact_id: str = ""
    content_snippet: str = ""
    status: str = "forgotten"


class ConfirmResult(BaseModel):
    """Result of confirm_fact."""

    fact_id: str = ""
    status: str = "confirmed"


def build_tools(
    storage: Any,
    retriever: HierarchicalRetriever | None = None,
    embed_fn: Callable[..., Any] | None = None,
    pipeline: Any = None,
    token_budget: int = 2000,
    get_maintenance_info: Callable[[], dict] | None = None,
    dedup_threshold: float = 0.90,
) -> list[Any]:
    """Build Orchestrator tools. Phase 3: full v3 tool set when retriever provided."""

    @function_tool
    async def memory_stats() -> MemoryStatsResult:
        """Graph-level memory metrics: episodes, facts, entities, communities."""
        stats = await storage.get_graph_stats()
        unconsolidated = await storage.get_unconsolidated_sessions()
        size_mb = storage.get_storage_size_mb()
        last_consolidation = None
        last_decay_run = None
        if get_maintenance_info:
            maint = get_maintenance_info()
            last_consolidation = maint.get("last_consolidation")
            last_decay_run = maint.get("last_decay_run")
        if last_consolidation is None or last_decay_run is None:
            persisted = await storage.get_maintenance_timestamps()
            if last_consolidation is None:
                last_consolidation = persisted.get("last_consolidation")
            if last_decay_run is None:
                last_decay_run = persisted.get("last_decay_run")
        return MemoryStatsResult(
            episodes=stats["episodes"],
            facts=stats["facts"],
            entities=stats["entities"],
            communities=stats["communities"],
            pending_queue_items=stats.get("pending_queue_items", 0),
            unconsolidated_sessions=len(unconsolidated),
            storage_size_mb=size_mb,
            last_consolidation=last_consolidation,
            last_decay_run=last_decay_run,
        )

    # When retriever is None (Phase 1/2), return only memory_stats
    if not retriever:
        return [memory_stats]

    @function_tool(strict_mode=False)
    async def search_memory(
        query: str,
        entity_name: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 10,
    ) -> SearchResult:
        """Search long-term memory. Returns relevant facts from the entity-fact graph.
        entity_name: filter by entity. after/before: time filter (last_week, last_month, YYYY-MM-DD).
        """
        query_embedding = None
        if embed_fn:
            emb = embed_fn(query)
            if asyncio.iscoroutine(emb):
                emb = await emb
            query_embedding = emb if isinstance(emb, list) else getattr(emb, "embedding", None)
        event_after = parse_time_expression(after) if after else None
        event_before = parse_time_expression(before) if before else None
        results = await retriever.search(
            query,
            query_embedding=query_embedding,
            limit=limit,
            token_budget=token_budget,
            entity_name=entity_name,
            event_after=event_after,
            event_before=event_before,
        )
        entities_map = await storage.get_entities_for_facts([f["id"] for f in results])
        enriched = []
        for r in results:
            subj = entities_map.get(r["subject_id"], {}).get("name", r["subject_id"])
            obj = entities_map.get(r["object_id"], {}).get("name", r["object_id"])
            enriched.append({
                "id": r["id"],
                "fact_text": r.get("fact_text", ""),
                "predicate": r.get("predicate", ""),
                "subject": subj,
                "object": obj,
                "confidence": r.get("confidence", 1.0),
                "t_valid": r.get("t_valid") or r.get("t_created"),
            })
        logger.info("search_memory: query=%r results=%d", query[:60], len(enriched))
        return SearchResult(results=enriched, count=len(enriched))

    @function_tool(strict_mode=False)
    async def remember_fact(fact: str, confidence: float = 1.0) -> RememberResult:
        """Explicitly save a fact to long-term memory.
        Use lower confidence (0.4-0.9) for uncertain or hedged statements.
        """
        if not fact or not fact.strip():
            return RememberResult(fact_id="", status="error: empty fact")
        safe_confidence = max(0.0, min(1.0, float(confidence)))
        if not pipeline:
            return RememberResult(fact_id="", status="error: pipeline not available")
        fact_id, status = await pipeline.remember_fact_from_text(
            fact.strip(), confidence=safe_confidence
        )
        logger.info("remember_fact: fact_id=%s status=%s", fact_id[:8] if fact_id else "", status)
        return RememberResult(fact_id=fact_id or "", status=status)

    @function_tool
    async def correct_fact(old_fact: str, new_fact: str) -> CorrectResult:
        """Correct a remembered fact: expire old, create replacement with invalidated_by chain."""
        if not old_fact or not new_fact:
            return CorrectResult(
                old_fact_id="",
                new_fact_id="",
                status="error: both old_fact and new_fact required",
            )
        query_embedding = None
        if embed_fn:
            emb = embed_fn(old_fact.strip())
            if asyncio.iscoroutine(emb):
                emb = await emb
            query_embedding = emb if isinstance(emb, list) else getattr(emb, "embedding", None)
        candidates = await retriever.search(
            old_fact.strip(),
            query_embedding=query_embedding,
            limit=5,
        )
        if not candidates:
            return CorrectResult(
                old_fact_id="",
                new_fact_id="",
                status="error: no matching fact found",
            )
        old_f = candidates[0]
        old_fact_id = old_f["id"]
        if not pipeline:
            return CorrectResult(
                old_fact_id=old_fact_id,
                new_fact_id="",
                status="error: pipeline not available",
            )
        new_fact_id, status = await pipeline.remember_fact_from_text(new_fact.strip())
        if not new_fact_id:
            return CorrectResult(
                old_fact_id=old_fact_id,
                new_fact_id="",
                status=f"error: could not create replacement: {status}",
            )
        await storage.expire_fact(old_fact_id, invalidated_by=new_fact_id)
        logger.info("correct_fact: old=%s new=%s", old_fact_id[:8], new_fact_id[:8])
        return CorrectResult(
            old_fact_id=old_fact_id,
            new_fact_id=new_fact_id,
            status="corrected",
        )

    @function_tool
    async def get_entity_info(entity_name: str) -> EntityInfoResult:
        """Get entity profile: summary, related facts."""
        if not entity_name or not entity_name.strip():
            return EntityInfoResult(status="error: no entity name provided")
        entity = await _resolve_entity(storage, entity_name.strip())
        if not entity:
            return EntityInfoResult(
                status=f"error: entity not found for '{entity_name}'"
            )
        facts_raw = await storage.get_facts_for_entity(entity["id"], limit=20)
        entities_map = await storage.get_entities_for_facts([f["id"] for f in facts_raw])
        facts_str = []
        for f in facts_raw:
            subj = entities_map.get(f["subject_id"], {}).get("name", f["subject_id"])
            obj = entities_map.get(f["object_id"], {}).get("name", f["object_id"])
            pred = f.get("predicate", "RELATED")
            text = f.get("fact_text", "")
            facts_str.append(f"[{subj}] --[{pred}]--> [{obj}]: {text}")
        return EntityInfoResult(
            entity_id=entity["id"],
            name=entity.get("name", entity.get("canonical_name", "")),
            summary=(entity.get("summary") or ""),
            facts=facts_str,
            status="ok",
        )

    @function_tool(strict_mode=False)
    async def get_timeline(
        entity_name: str = "",
        after: str = "",
        before: str = "",
        limit: int = 50,
    ) -> TimelineResult:
        """Get chronological facts. Optional: filter by entity name, time range
        (last_week, last_month, YYYY-MM-DD)."""
        entity_id = None
        if entity_name and entity_name.strip():
            entity = await _resolve_entity(storage, entity_name.strip())
            if entity:
                entity_id = entity["id"]
            else:
                return TimelineResult(
                    status=f"error: no entity found for '{entity_name}'"
                )
        event_after = parse_time_expression(after) if after and after.strip() else None
        event_before = parse_time_expression(before) if before and before.strip() else None
        results = await retriever.get_timeline(
            entity_id=entity_id,
            after=event_after,
            before=event_before,
            limit=limit,
        )
        if not results:
            return TimelineResult(
                status="error: no events found for the given criteria"
            )
        entities_map = await storage.get_entities_for_facts([r["id"] for r in results])
        events = []
        for r in results:
            ts_val = r.get("t_valid") or r.get("t_created")
            ts_str = str(ts_val) if ts_val else "?"
            try:
                from datetime import datetime
                dt = datetime.fromtimestamp(ts_val) if ts_val else None
                ts_str = dt.isoformat() if dt else "?"
            except (ValueError, OSError):
                pass
            subj = entities_map.get(r["subject_id"], {}).get("name", r["subject_id"])
            obj = entities_map.get(r["object_id"], {}).get("name", r["object_id"])
            content = f"[{subj}] --[{r.get('predicate','')}]--> [{obj}]: {(r.get('fact_text') or '')[:150]}"
            if len(r.get("fact_text") or "") > 150:
                content += "..."
            events.append(TimelineEvent(id=r.get("id", ""), timestamp=ts_str, content=content))
        return TimelineResult(events=events, count=len(events), status="ok")

    @function_tool
    async def forget_fact(fact: str) -> ForgetResult:
        """Forget (expire) a memory. Use ONLY when the user explicitly asks to forget."""
        if not fact or not fact.strip():
            return ForgetResult(status="error: no fact provided")
        query_embedding = None
        if embed_fn:
            emb = embed_fn(fact.strip())
            if asyncio.iscoroutine(emb):
                emb = await emb
            query_embedding = emb if isinstance(emb, list) else getattr(emb, "embedding", None)
        candidates = await retriever.search(
            fact.strip(),
            query_embedding=query_embedding,
            limit=5,
        )
        if not candidates:
            return ForgetResult(
                status=f"error: no memory found matching '{fact[:100]}'"
            )
        f = candidates[0]
        fact_id = f["id"]
        content_snippet = (f.get("fact_text") or "")[:100]
        if len(f.get("fact_text") or "") > 100:
            content_snippet += "..."
        await storage.expire_fact(fact_id, invalidated_by=None)
        logger.info("forget_fact: expired fact_id=%s", fact_id[:8])
        return ForgetResult(
            fact_id=fact_id,
            content_snippet=content_snippet,
            status="forgotten",
        )

    @function_tool
    async def confirm_fact(fact: str) -> ConfirmResult:
        """Protect a memory from decay. Use when the user confirms a fact is important or correct.
        Sets confidence=1.0 so the fact never expires due to Ebbinghaus decay."""
        if not fact or not fact.strip():
            return ConfirmResult(status="error: no fact provided")
        query_embedding = None
        if embed_fn:
            emb = embed_fn(fact.strip())
            if asyncio.iscoroutine(emb):
                emb = await emb
            query_embedding = emb if isinstance(emb, list) else getattr(emb, "embedding", None)
        candidates = await retriever.search(
            fact.strip(),
            query_embedding=query_embedding,
            limit=5,
        )
        if not candidates:
            return ConfirmResult(
                status=f"error: no memory found matching '{fact[:100]}'"
            )
        f = candidates[0]
        fact_id = f["id"]
        await storage.update_fact_confidence(fact_id, 1.0)
        logger.info("confirm_fact: protected fact_id=%s", fact_id[:8])
        return ConfirmResult(fact_id=fact_id, status="confirmed")

    return [
        search_memory,
        remember_fact,
        correct_fact,
        get_entity_info,
        get_timeline,
        memory_stats,
        forget_fact,
        confirm_fact,
    ]
