"""Orchestrator tools for Memory v2. Phase 1: search_memory only."""

from typing import Any

from agents import function_tool
from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """Result of search_memory."""

    results: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


def build_tools(
    retrieval: Any,
    token_budget: int = 2000,
) -> list[Any]:
    """Build Orchestrator tools. Phase 1: search_memory only."""

    @function_tool
    async def search_memory(
        query: str,
        type: str | None = None,
        limit: int = 10,
    ) -> SearchResult:
        """Search long-term memory. Returns relevant facts and knowledge.
        Use type='episodic' for conversation history, or omit for semantic/procedural/opinion.
        """
        node_types = None
        if type:
            if type in ("episodic", "semantic", "procedural", "opinion"):
                node_types = [type]
            else:
                return SearchResult(
                    results=[],
                    count=0,
                )
        results = await retrieval.search(
            query,
            limit=limit,
            token_budget=token_budget,
            node_types=node_types,
        )
        return SearchResult(results=results, count=len(results))

    @function_tool
    async def remember_fact(fact: str) -> str:
        """Explicitly save a fact. Phase 2+."""
        return "remember_fact not yet available (Phase 2)"

    @function_tool
    async def correct_fact(old_fact: str, new_fact: str) -> str:
        """Correct a remembered fact. Phase 2+."""
        return "correct_fact not yet available (Phase 2)"

    @function_tool
    async def confirm_fact(fact_id: str) -> str:
        """Confirm a fact is accurate. Phase 2+."""
        return "confirm_fact not yet available (Phase 2)"

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
