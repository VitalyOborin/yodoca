"""Write-path agent tools. Internal to MemoryAgent, not exposed to Orchestrator."""

import logging
import time
import uuid
from typing import Any, Callable

from agents import function_tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SaveBatchResult(BaseModel):
    """Result of save_nodes_batch."""

    node_ids: list[str] = Field(default_factory=list)
    count: int = 0


class EntityResult(BaseModel):
    """Result of extract_and_link_entities."""

    entities_created: int = 0
    entities_linked: int = 0


class NodeInput(BaseModel):
    """Input for save_nodes_batch. One extracted node."""

    type: str = Field(description="semantic, procedural, or opinion")
    content: str = Field(description="Node content")
    source_episode_ids: list[str] = Field(
        default_factory=list,
        description="IDs of source episodic nodes",
    )


class EntityMention(BaseModel):
    """Entity mention for extract_and_link_entities."""

    canonical_name: str = Field(description="Canonical entity name")
    type: str = Field(
        description="person, project, organization, place, concept, or tool"
    )
    aliases: list[str] = Field(default_factory=list, description="Alternative names")


class EntityInput(BaseModel):
    """Input for extract_and_link_entities. Node with its entity mentions."""

    node_id: str = Field(description="Node ID to link")
    entities: list[EntityMention] = Field(
        default_factory=list,
        description="Entity mentions in this node",
    )


class CausalEdgeInput(BaseModel):
    """Input for save_causal_edges. Cause-effect between episodes."""

    source_id: str = Field(description="Cause episode node ID")
    target_id: str = Field(description="Effect episode node ID")
    predicate: str = Field(
        default="caused_by",
        description="Human-readable label, e.g. caused_by, led_to",
    )


def build_write_path_tools(
    storage: Any,
    retrieval: Any,
    embed_fn: Callable[..., Any] | None,
    embed_batch_fn: Callable[..., Any] | None,
) -> list[Any]:
    """Build internal tools for the write-path memory agent."""

    @function_tool
    async def is_session_consolidated(session_id: str) -> bool:
        """Check if session was already consolidated. Idempotency guard."""
        result = await storage.is_session_consolidated(session_id)
        logger.debug("is_session_consolidated(%s) = %s", session_id, result)
        return result

    @function_tool
    async def get_session_episodes(
        session_id: str,
        limit: int = 30,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch episodic nodes for a session. Paginated, ordered by event_time."""
        episodes = await storage.get_session_episodes(
            session_id, limit=limit, offset=offset
        )
        logger.debug("get_session_episodes(%s): returned %d episodes", session_id, len(episodes))
        return episodes

    @function_tool
    async def save_nodes_batch(nodes: list[NodeInput]) -> SaveBatchResult:
        """Save extracted nodes (semantic/procedural/opinion) with derived_from edges to source episodes."""
        if not nodes:
            return SaveBatchResult(node_ids=[], count=0)
        now = int(time.time())
        items: list[tuple[dict[str, Any], list[str]]] = []
        for n in nodes:
            n_type = n.type
            content = (n.content or "").strip()
            source_ids = n.source_episode_ids or []
            if n_type not in ("semantic", "procedural", "opinion") or not content:
                continue
            items.append((
                {
                    "type": n_type,
                    "content": content,
                    "event_time": now,
                    "created_at": now,
                    "valid_from": now,
                    "source_type": "consolidation",
                    "source_role": "memory_agent",
                    "confidence": 0.8,
                },
                source_ids if isinstance(source_ids, list) else [],
            ))
        if not items:
            return SaveBatchResult(node_ids=[], count=0)
        node_dicts = [it[0] for it in items]
        source_ids_per_node = [it[1] for it in items]
        logger.info("save_nodes_batch: saving %d nodes", len(node_dicts))
        node_ids = await storage.insert_nodes_batch(node_dicts)
        for nid, ep_ids in zip(node_ids, source_ids_per_node):
            for ep_id in ep_ids:
                await storage.insert_edge_awaitable({
                    "source_id": nid,
                    "target_id": ep_id,
                    "relation_type": "derived_from",
                    "valid_from": now,
                    "created_at": now,
                })
        if embed_batch_fn:
            texts = [nd["content"] for nd in node_dicts]
            embeddings = await embed_batch_fn(texts)
            for nid, emb in zip(node_ids, embeddings):
                if emb:
                    await storage.save_embedding(nid, emb)
        logger.info("save_nodes_batch: saved %d nodes with embeddings", len(node_ids))
        return SaveBatchResult(node_ids=node_ids, count=len(node_ids))

    @function_tool
    async def extract_and_link_entities(nodes: list[EntityInput]) -> EntityResult:
        """Link nodes to entity anchors. Resolve to existing or create new entities."""
        created = 0
        linked = 0
        now = int(time.time())
        for inp in nodes:
            node_id = inp.node_id
            entities = inp.entities or []
            if not node_id:
                continue
            for ment in entities:
                m_type = ment.type
                canonical = ment.canonical_name
                aliases = ment.aliases or []
                if m_type not in (
                    "person", "project", "organization", "place", "concept", "tool"
                ) or not canonical:
                    continue
                entity = await storage.get_entity_by_name(canonical)
                if not entity:
                    for alias in aliases:
                        entity = await storage.search_entity_by_alias(str(alias))
                        if entity:
                            break
                if entity:
                    await storage.link_node_entity(node_id, entity["id"])
                    await storage.update_entity(
                        entity["id"],
                        {
                            "mention_count": entity["mention_count"] + 1,
                            "last_updated": now,
                        },
                    )
                    linked += 1
                else:
                    entity_id = str(uuid.uuid4())
                    await storage.insert_entity({
                        "id": entity_id,
                        "canonical_name": canonical,
                        "type": m_type,
                        "aliases": aliases if aliases else [canonical],
                        "first_seen": now,
                        "last_updated": now,
                        "mention_count": 1,
                    })
                    await storage.link_node_entity(node_id, entity_id)
                    created += 1
                    linked += 1
        logger.info("extract_and_link_entities: created=%d linked=%d", created, linked)
        return EntityResult(entities_created=created, entities_linked=linked)

    @function_tool
    async def detect_conflicts(fact: str) -> list[dict[str, Any]]:
        """Find potentially contradicting facts via hybrid search. Returns top 5 candidates."""
        query_embedding = None
        if embed_fn and fact:
            query_embedding = await embed_fn(fact)
        results = await retrieval.search(
            fact,
            query_embedding=query_embedding,
            limit=5,
            node_types=["semantic", "procedural", "opinion"],
        )
        logger.debug("detect_conflicts: %d candidates for %r", len(results), fact[:60])
        return [
            {"id": r["id"], "type": r["type"], "content": r["content"], "confidence": r.get("confidence")}
            for r in results
        ]

    @function_tool
    async def resolve_conflict(old_node_id: str, new_node_id: str) -> str:
        """Resolve conflict: soft-delete old node, create supersedes edge."""
        now = int(time.time())
        await storage.update_node_fields(
            old_node_id,
            {"confidence": 0.3, "decay_rate": 0.5},
        )
        await storage.soft_delete_node(old_node_id)
        await storage.insert_edge_awaitable({
            "source_id": new_node_id,
            "target_id": old_node_id,
            "relation_type": "supersedes",
            "valid_from": now,
            "created_at": now,
        })
        logger.info("resolve_conflict: %s supersedes %s", new_node_id[:8], old_node_id[:8])
        return "conflict resolved"

    @function_tool
    async def mark_session_consolidated(session_id: str) -> str:
        """Mark session as consolidated. Call only after all extraction is done."""
        await storage.mark_session_consolidated(session_id)
        logger.info("mark_session_consolidated: %s", session_id)
        return f"session {session_id} marked consolidated"

    @function_tool
    async def save_causal_edges(edges: list[CausalEdgeInput]) -> str:
        """Create causal edges between episode pairs. source_id=cause, target_id=effect. confidence=0.7."""
        if not edges:
            return "no edges to save"
        now = int(time.time())
        for e in edges:
            if not e.source_id or not e.target_id:
                continue
            await storage.insert_edge_awaitable({
                "source_id": e.source_id,
                "target_id": e.target_id,
                "relation_type": "causal",
                "predicate": (e.predicate or "caused_by").strip() or "caused_by",
                "confidence": 0.7,
                "valid_from": now,
                "created_at": now,
            })
        count = len([x for x in edges if x.source_id and x.target_id])
        logger.info("save_causal_edges: %d edges saved", count)
        return f"saved {count} causal edges"

    @function_tool
    async def update_entity_summary(entity_id: str, summary: str) -> str:
        """Update entity summary and re-embed for improved vector search. Used for entity enrichment."""
        if not entity_id or not (summary or "").strip():
            return "entity_id and summary required"
        now = int(time.time())
        await storage.update_entity(
            entity_id, {"summary": summary.strip(), "last_updated": now}
        )
        if embed_fn:
            emb = await embed_fn(summary.strip())
            if emb:
                await storage.save_entity_embedding(entity_id, emb)
        logger.info("update_entity_summary: entity=%s", entity_id[:8])
        return f"entity {entity_id} summary updated"

    return [
        is_session_consolidated,
        get_session_episodes,
        save_nodes_batch,
        extract_and_link_entities,
        detect_conflicts,
        resolve_conflict,
        mark_session_consolidated,
        save_causal_edges,
        update_entity_summary,
    ]
