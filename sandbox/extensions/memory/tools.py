"""Orchestrator tools for Memory v2. Phase 2: search_memory, remember_fact, correct_fact, confirm_fact."""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from agents import function_tool
from pydantic import BaseModel, Field

from retrieval import parse_time_expression, _resolve_entity

logger = logging.getLogger(__name__)

# Resolve local timezone once at import time so every call is consistent and cheap.
_LOCAL_TZ = datetime.now(timezone.utc).astimezone().tzinfo


def _format_event_time(ts: int | None) -> dict[str, str]:
    """Return human-readable timestamp fields derived from a Unix epoch integer.

    Returns a dict with:
      - event_time_iso:   RFC 3339 in UTC, e.g. "2026-02-23T15:23:47+00:00"
      - event_time_local: local wall-clock, e.g. "2026-02-23 18:23:47 UTC+3"
      - event_time_tz:    timezone label, e.g. "UTC+3"

    All fields are empty strings when ts is None, 0, or non-positive.
    """
    if not ts or ts <= 0:
        return {"event_time_iso": "", "event_time_local": "", "event_time_tz": ""}
    utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    local_dt = utc_dt.astimezone(_LOCAL_TZ)
    tz_name = local_dt.strftime("%Z")
    return {
        "event_time_iso": utc_dt.isoformat(),
        "event_time_local": local_dt.strftime(f"%Y-%m-%d %H:%M:%S {tz_name}"),
        "event_time_tz": tz_name,
    }


class SearchResult(BaseModel):
    """Result of search_memory."""

    results: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class RememberResult(BaseModel):
    """Result of remember_fact."""

    node_id: str
    status: str = "saved"


class CorrectResult(BaseModel):
    """Result of correct_fact."""

    old_node_id: str
    new_node_id: str
    status: str = "corrected"


class ConfirmResult(BaseModel):
    """Result of confirm_fact."""

    node_id: str
    status: str = "confirmed"


class EntityInfoResult(BaseModel):
    """Result of get_entity_info."""

    entity_id: str = ""
    canonical_name: str = ""
    summary: str = ""
    facts: list[str] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    status: str = "ok"


class MemoryStatsResult(BaseModel):
    """Result of memory_stats."""

    nodes: dict[str, int] = Field(default_factory=dict)
    edges: dict[str, int] = Field(default_factory=dict)
    entities: int = 0
    orphan_nodes: int = 0
    avg_edges_per_node: float = 0.0
    unconsolidated_sessions: int = 0
    storage_size_mb: float = 0.0
    last_consolidation: str | None = None
    last_decay_run: str | None = None


class NodeRef(BaseModel):
    """Reference to a node in provenance chain."""

    id: str
    content: str = ""


class EntityRef(BaseModel):
    """Reference to an entity in provenance chain."""

    canonical_name: str
    type: str = ""


class ExplainFactResult(BaseModel):
    """Result of explain_fact."""

    fact_id: str = ""
    fact_type: str = ""
    fact_content: str = ""
    source_episodes: list[NodeRef] = Field(default_factory=list)
    supersedes: list[NodeRef] = Field(default_factory=list)
    superseded_by: list[NodeRef] = Field(default_factory=list)
    entities: list[EntityRef] = Field(default_factory=list)
    status: str = "ok"


class WeakFact(BaseModel):
    """Single weak fact entry."""

    id: str
    content: str
    confidence: float = 0.0
    last_accessed: int | None = None


class WeakFactsResult(BaseModel):
    """Result of weak_facts."""

    facts: list[WeakFact] = Field(default_factory=list)
    threshold: float = 0.3


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

    node_id: str = ""
    content_snippet: str = ""
    status: str = "forgotten"


def build_tools(
    retrieval: Any,
    storage: Any,
    embed_fn: Callable[..., Any] | None,
    token_budget: int = 2000,
    get_maintenance_info: Callable[[], dict] | None = None,
) -> list[Any]:
    """Build Orchestrator tools. Phase 2: search, remember, correct, confirm."""

    @function_tool
    async def search_memory(
        query: str,
        type: str | None = None,
        entity_name: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 10,
    ) -> SearchResult:
        """Search long-term memory. Returns relevant facts and knowledge.
        Use type='episodic' for conversation history, or omit for all types.
        entity_name: filter by entity. after/before: time filter (last_week, last_month, YYYY-MM-DD).
        """
        if type and type not in ("episodic", "semantic", "procedural", "opinion"):
            return SearchResult(results=[], count=0)
        node_types = [type] if type else ["episodic", "semantic", "procedural", "opinion"]
        query_embedding = None
        if embed_fn:
            query_embedding = await embed_fn(query)
        event_after = parse_time_expression(after)
        event_before = parse_time_expression(before)
        results = await retrieval.search(
            query,
            query_embedding=query_embedding,
            limit=limit,
            token_budget=token_budget,
            node_types=node_types,
            entity_name=entity_name,
            event_after=event_after,
            event_before=event_before,
        )
        enriched = [{**r, **_format_event_time(r.get("event_time"))} for r in results]
        logger.info("search_memory: query=%r types=%s results=%d", query[:60], node_types, len(enriched))
        return SearchResult(results=enriched, count=len(enriched))

    @function_tool
    async def remember_fact(fact: str) -> RememberResult:
        """Explicitly save a fact to long-term memory."""
        if not fact or not fact.strip():
            return RememberResult(node_id="", status="error: empty fact")
        now = int(time.time())
        node_id = str(uuid.uuid4())
        node = {
            "id": node_id,
            "type": "semantic",
            "content": fact.strip(),
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "source_type": "conversation",
            "source_role": "orchestrator",
            "confidence": 1.0,
        }
        await storage.insert_node_awaitable(node)
        if embed_fn:
            embedding = await embed_fn(fact.strip())
            if embedding:
                await storage.save_embedding(node_id, embedding)
        logger.info("remember_fact: node=%s len=%d", node_id[:8], len(fact.strip()))
        return RememberResult(node_id=node_id, status="saved")

    @function_tool
    async def correct_fact(old_fact: str, new_fact: str) -> CorrectResult:
        """Correct a remembered fact: soft-delete old, create new with supersedes edge."""
        if not old_fact or not new_fact:
            return CorrectResult(
                old_node_id="",
                new_node_id="",
                status="error: both old_fact and new_fact required",
            )
        query_embedding = await embed_fn(old_fact.strip()) if embed_fn else None
        candidates = await retrieval.search(
            old_fact,
            query_embedding=query_embedding,
            limit=5,
            node_types=["semantic", "procedural", "opinion"],
        )
        if not candidates:
            return CorrectResult(
                old_node_id="",
                new_node_id="",
                status="error: no matching fact found",
            )
        old_node = candidates[0]
        old_node_id = old_node["id"]
        now = int(time.time())
        await storage.update_node_fields(
            old_node_id,
            {"confidence": 0.3, "decay_rate": 0.5},
        )
        await storage.soft_delete_node(old_node_id)
        new_node_id = str(uuid.uuid4())
        new_node = {
            "id": new_node_id,
            "type": "semantic",
            "content": new_fact.strip(),
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "source_type": "conversation",
            "source_role": "orchestrator",
            "confidence": 1.0,
        }
        await storage.insert_node_awaitable(new_node)
        storage.insert_edge({
            "source_id": new_node_id,
            "target_id": old_node_id,
            "relation_type": "supersedes",
            "valid_from": now,
            "created_at": now,
        })
        if embed_fn:
            embedding = await embed_fn(new_fact.strip())
            if embedding:
                await storage.save_embedding(new_node_id, embedding)
        logger.info("correct_fact: old=%s new=%s", old_node_id[:8], new_node_id[:8])
        return CorrectResult(
            old_node_id=old_node_id,
            new_node_id=new_node_id,
            status="corrected",
        )

    @function_tool
    async def confirm_fact(fact_id: str) -> ConfirmResult:
        """Confirm a fact is accurate. Sets confidence=1.0, decay_rate=0.0."""
        node = await storage.get_node(fact_id)
        if not node:
            return ConfirmResult(node_id=fact_id, status="error: node not found")
        await storage.update_node_fields(
            fact_id,
            {"confidence": 1.0, "decay_rate": 0.0},
        )
        logger.info("confirm_fact: %s", fact_id[:8])
        return ConfirmResult(node_id=fact_id, status="confirmed")

    @function_tool
    async def get_entity_info(entity_name: str) -> EntityInfoResult:
        """Get entity profile: summary, related facts, timeline."""
        if not entity_name or not entity_name.strip():
            return EntityInfoResult(status="error: no entity name provided")
        entity = await _resolve_entity(storage, entity_name)
        if not entity:
            return EntityInfoResult(status=f"error: entity not found for '{entity_name}'")
        nodes = await storage.entity_nodes_for_entity(
            entity["id"],
            node_types=["semantic", "procedural", "opinion", "episodic"],
            limit=20,
        )
        facts = [n.get("content", "") for n in nodes if n["type"] in ("semantic", "procedural", "opinion")][:10]
        episodes = sorted([n for n in nodes if n["type"] == "episodic"], key=lambda x: x.get("event_time", 0))[:10]
        timeline = [ep.get("content", "") for ep in episodes]
        return EntityInfoResult(
            entity_id=entity["id"],
            canonical_name=entity.get("canonical_name", ""),
            summary=entity.get("summary") or "",
            facts=facts,
            timeline=timeline,
            status="ok",
        )

    @function_tool
    async def memory_stats() -> MemoryStatsResult:
        """Graph-level memory metrics: node/edge counts, entities, data quality indicators."""
        stats = await storage.get_graph_stats()
        unconsolidated = await storage.get_unconsolidated_sessions()
        size_mb = storage.get_storage_size_mb()
        last_consolidation = None
        last_decay_run = None
        if get_maintenance_info:
            maint = get_maintenance_info()
            last_consolidation = maint.get("last_consolidation")
            last_decay_run = maint.get("last_decay_run")
        return MemoryStatsResult(
            nodes=stats["nodes"],
            edges=stats["edges"],
            entities=stats["entities"],
            orphan_nodes=stats["orphan_nodes"],
            avg_edges_per_node=stats["avg_edges_per_node"],
            unconsolidated_sessions=len(unconsolidated),
            storage_size_mb=size_mb,
            last_consolidation=last_consolidation,
            last_decay_run=last_decay_run,
        )

    def _trunc(s: str, n: int = 80) -> str:
        c = (s or "")[:n]
        return c + "..." if len(s or "") > n else c

    @function_tool
    async def explain_fact(fact_id: str) -> ExplainFactResult:
        """Explain provenance of a fact: source episodes, superseded facts, linked entities."""
        if not fact_id or not fact_id.strip():
            return ExplainFactResult(status="error: no fact_id provided")
        chain = await storage.get_provenance_chain(fact_id.strip())
        if not chain["node"]:
            return ExplainFactResult(status=f"error: fact '{fact_id}' not found")
        node = chain["node"]
        source_episodes = [
            NodeRef(id=ep.get("id", ""), content=_trunc(ep.get("content", "")))
            for ep in chain.get("source_episodes", [])
        ]
        supersedes = [
            NodeRef(id=s.get("id", ""), content=_trunc(s.get("content", "")))
            for s in chain.get("supersedes", [])
        ]
        superseded_by = [
            NodeRef(id=s.get("id", ""), content=_trunc(s.get("content", "")))
            for s in chain.get("superseded_by", [])
        ]
        entities = [
            EntityRef(canonical_name=ent.get("canonical_name", ""), type=ent.get("type", ""))
            for ent in chain.get("entities", [])
        ]
        return ExplainFactResult(
            fact_id=node.get("id", ""),
            fact_type=node.get("type", ""),
            fact_content=node.get("content", ""),
            source_episodes=source_episodes,
            supersedes=supersedes,
            superseded_by=superseded_by,
            entities=entities,
            status="ok",
        )

    @function_tool
    async def weak_facts(threshold: float = 0.3, limit: int = 10) -> WeakFactsResult:
        """List facts with low confidence that may need confirmation or will decay soon."""
        nodes = await storage.get_weak_nodes(threshold=threshold, limit=limit)
        facts = [
            WeakFact(
                id=n.get("id", ""),
                content=(n.get("content", "") or "")[:60] + ("..." if len(n.get("content", "") or "") > 60 else ""),
                confidence=n.get("confidence", 0.0),
                last_accessed=n.get("last_accessed"),
            )
            for n in nodes
        ]
        return WeakFactsResult(facts=facts, threshold=threshold)

    @function_tool
    async def get_timeline(
        entity_name: str = "",
        after: str = "",
        before: str = "",
        limit: int = 50,
    ) -> TimelineResult:
        """Get chronological events. Optional: filter by entity name, time range
        (last_week, last_month, YYYY-MM-DD)."""
        entity_id = None
        if entity_name and entity_name.strip():
            entity = await _resolve_entity(storage, entity_name.strip())
            if entity:
                entity_id = entity["id"]
            else:
                return TimelineResult(status=f"error: no entity found for '{entity_name}'")
        event_after = parse_time_expression(after) if after and after.strip() else None
        event_before = parse_time_expression(before) if before and before.strip() else None
        results = await storage.get_timeline(
            entity_id=entity_id,
            event_after=event_after,
            event_before=event_before,
            limit=limit,
        )
        if not results:
            return TimelineResult(status="error: no events found for the given criteria")
        events = []
        for r in results:
            fmt = _format_event_time(r.get("event_time"))
            ts = fmt["event_time_iso"] or "?"
            content = (r.get("content", "") or "")[:200]
            if len(r.get("content", "") or "") > 200:
                content += "..."
            events.append(TimelineEvent(id=r.get("id", ""), timestamp=ts, content=content))
        return TimelineResult(events=events, count=len(events), status="ok")

    @function_tool
    async def forget_fact(fact: str) -> ForgetResult:
        """Forget (soft-delete) a memory. Use ONLY when the user explicitly asks to forget or remove something."""
        if not fact or not fact.strip():
            return ForgetResult(status="error: no fact provided")
        query_embedding = await embed_fn(fact.strip()) if embed_fn else None
        candidates = await retrieval.search(
            fact,
            query_embedding=query_embedding,
            limit=5,
            node_types=["semantic", "procedural", "opinion", "episodic"],
        )
        if not candidates:
            return ForgetResult(status=f"error: no memory found matching '{fact[:100]}'")
        node = candidates[0]
        node_id = node["id"]
        content_snippet = (node.get("content", "") or "")[:100]
        if len(node.get("content", "") or "") > 100:
            content_snippet += "..."
        await storage.soft_delete_node(node_id)
        logger.info("forget_fact: deleted node=%s", node_id[:8])
        return ForgetResult(node_id=node_id, content_snippet=content_snippet, status="forgotten")

    return [
        search_memory,
        remember_fact,
        correct_fact,
        confirm_fact,
        get_entity_info,
        get_timeline,
        memory_stats,
        explain_fact,
        forget_fact,
        weak_facts,
    ]
