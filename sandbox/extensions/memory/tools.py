"""Orchestrator tools for Memory v2. Phase 2: search_memory, remember_fact, correct_fact, confirm_fact."""

import time
import uuid
from typing import Any, Callable

from agents import function_tool
from pydantic import BaseModel, Field


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
) -> list[Any]:
    """Build Orchestrator tools. Phase 2: search, remember, correct, confirm."""

    @function_tool
    async def search_memory(
        query: str,
        type: str | None = None,
        limit: int = 10,
    ) -> SearchResult:
        """Search long-term memory. Returns relevant facts and knowledge.
        Use type='episodic' for conversation history, or omit for all types.
        """
        if type and type not in ("episodic", "semantic", "procedural", "opinion"):
            return SearchResult(results=[], count=0)
        node_types = [type] if type else ["episodic", "semantic", "procedural", "opinion"]
        query_embedding = None
        if embed_fn:
            query_embedding = await embed_fn(query)
        results = await retrieval.search(
            query,
            query_embedding=query_embedding,
            limit=limit,
            token_budget=token_budget,
            node_types=node_types,
        )
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
        return ConfirmResult(node_id=fact_id, status="confirmed")

    @function_tool
    async def get_entity_info(entity_name: str) -> str:
        """Get entity profile and related facts. Phase 2+."""
        return "get_entity_info not yet available (Phase 2)"

    @function_tool
    async def memory_stats() -> str:
        """Graph-level memory metrics. Phase 6."""
        return "memory_stats not yet available (Phase 6)"

    return [
        search_memory,
        remember_fact,
        correct_fact,
        confirm_fact,
        get_entity_info,
        memory_stats,
    ]
