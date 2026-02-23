"""Orchestrator tools for Memory v2. Phase 2: search_memory, remember_fact, correct_fact, confirm_fact."""

import logging
import time
import uuid
from typing import Any, Callable

from agents import function_tool
from pydantic import BaseModel, Field

from retrieval import parse_time_expression, _resolve_entity

logger = logging.getLogger(__name__)


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
        logger.info("search_memory: query=%r types=%s results=%d", query[:60], node_types, len(results))
        return SearchResult(results=results, count=len(results))

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
    async def get_entity_info(entity_name: str) -> str:
        """Get entity profile: summary, related facts, timeline."""
        if not entity_name or not entity_name.strip():
            return "No entity name provided."
        entity = await _resolve_entity(storage, entity_name)
        if not entity:
            return f"No entity found for '{entity_name}'"
        nodes = await storage.entity_nodes_for_entity(
            entity["id"],
            node_types=["semantic", "procedural", "opinion", "episodic"],
            limit=20,
        )
        facts = [n for n in nodes if n["type"] in ("semantic", "procedural", "opinion")]
        episodes = [n for n in nodes if n["type"] == "episodic"]
        lines = ["## Entity: " + entity.get("canonical_name", "")]
        summary = entity.get("summary")
        if summary:
            lines.append(summary)
        if facts:
            lines.append("\n## Related facts")
            for n in facts[:10]:
                lines.append(f"- {n.get('content', '')}")
        if episodes:
            lines.append("\n## Timeline")
            for ep in sorted(episodes, key=lambda x: x.get("event_time", 0))[:10]:
                lines.append(f"- {ep.get('content', '')}")
        return "\n".join(lines)

    @function_tool
    async def memory_stats() -> str:
        """Graph-level memory metrics: node/edge counts, entities, data quality indicators."""
        stats = await storage.get_graph_stats()
        unconsolidated = await storage.get_unconsolidated_sessions()
        size_mb = storage.get_storage_size_mb()
        n = stats["nodes"]
        e = stats["edges"]
        lines = [
            f"Nodes: episodic {n['episodic']}, semantic {n['semantic']}, procedural {n['procedural']}, opinion {n['opinion']}",
            f"Edges: temporal {e['temporal']}, causal {e['causal']}, entity {e['entity']}, derived_from {e['derived_from']}, supersedes {e['supersedes']}",
            f"Entities: {stats['entities']}",
            f"Orphan nodes: {stats['orphan_nodes']}",
            f"Avg edges/node: {stats['avg_edges_per_node']}",
            f"Unconsolidated sessions: {len(unconsolidated)}",
            f"Storage size: {size_mb} MB",
        ]
        if get_maintenance_info:
            maint = get_maintenance_info()
            if maint.get("last_consolidation"):
                lines.append(f"Last consolidation: {maint['last_consolidation']}")
            if maint.get("last_decay_run"):
                lines.append(f"Last decay run: {maint['last_decay_run']}")
        return "\n".join(lines)

    @function_tool
    async def explain_fact(fact_id: str) -> str:
        """Explain provenance of a fact: source episodes, superseded facts, linked entities."""
        if not fact_id or not fact_id.strip():
            return "No fact_id provided."
        chain = await storage.get_provenance_chain(fact_id.strip())
        if not chain["node"]:
            return f"Fact '{fact_id}' not found."
        lines = ["## Fact"]
        node = chain["node"]
        lines.append(f"ID: {node.get('id', '')}")
        lines.append(f"Type: {node.get('type', '')}")
        lines.append(f"Content: {node.get('content', '')}")
        def _trunc(s: str, n: int = 80) -> str:
            c = (s or "")[:n]
            return c + "..." if len(s or "") > n else c

        if chain["source_episodes"]:
            lines.append("\n## Source episodes (derived_from)")
            for ep in chain["source_episodes"]:
                lines.append(f"- [{ep.get('id', '')}] {_trunc(ep.get('content', ''))}")
        if chain["supersedes"]:
            lines.append("\n## Supersedes (replaces)")
            for s in chain["supersedes"]:
                lines.append(f"- [{s.get('id', '')}] {_trunc(s.get('content', ''))}")
        if chain["superseded_by"]:
            lines.append("\n## Superseded by")
            for s in chain["superseded_by"]:
                lines.append(f"- [{s.get('id', '')}] {_trunc(s.get('content', ''))}")
        if chain["entities"]:
            lines.append("\n## Linked entities")
            for ent in chain["entities"]:
                lines.append(f"- {ent.get('canonical_name', '')} ({ent.get('type', '')})")
        if not any([chain["source_episodes"], chain["supersedes"], chain["superseded_by"], chain["entities"]]):
            lines.append("\n## Provenance")
            lines.append("No source episodes, supersedes edges, or linked entities.")
        return "\n".join(lines)

    @function_tool
    async def weak_facts(threshold: float = 0.3, limit: int = 10) -> str:
        """List facts with low confidence that may need confirmation or will decay soon."""
        nodes = await storage.get_weak_nodes(threshold=threshold, limit=limit)
        if not nodes:
            return f"No facts with confidence < {threshold}."
        def _trunc60(s: str) -> str:
            c = (s or "")[:60]
            return c + "..." if len(s or "") > 60 else c

        lines = [f"## Low-confidence facts (confidence < {threshold})"]
        for n in nodes:
            la = n.get("last_accessed")
            la_str = str(la) if la else "never"
            lines.append(f"- [{n.get('id', '')}] {_trunc60(n.get('content', ''))} | conf={n.get('confidence', 0):.2f} | last_accessed={la_str}")
        return "\n".join(lines)

    return [
        search_memory,
        remember_fact,
        correct_fact,
        confirm_fact,
        get_entity_info,
        memory_stats,
        explain_fact,
        weak_facts,
    ]
