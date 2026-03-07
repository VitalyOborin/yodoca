"""Deferred tool resolution for Orchestrator.

Provides a small gateway toolset so Orchestrator does not need all extension tools
in its own context. Instead, tools are resolved per task and executed by an
ephemeral dynamic agent.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from agents import function_tool
from pydantic import BaseModel, Field

from core.agents.factory import AgentFactory, AgentSpec
from core.agents.registry import AgentRegistry
from core.extensions.contract import AgentInvocationContext

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "you",
    "как",
    "что",
    "чтобы",
    "для",
    "или",
    "это",
    "эта",
    "эти",
    "при",
    "через",
    "нужно",
    "надо",
    "agent",
    "assistant",
    "task",
}


class ToolCatalogEntry(BaseModel):
    """Metadata for one tool ID available to dynamic agents."""

    tool_id: str
    name: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)


class ResolvedToolCandidate(BaseModel):
    """Candidate tool scored by resolver."""

    tool_id: str
    score: int


class ToolResolutionResult(BaseModel):
    """Structured result for resolver tool."""

    selected_tool_ids: list[str] = Field(default_factory=list)
    candidates: list[ResolvedToolCandidate] = Field(default_factory=list)


class DeferredExecutionResult(BaseModel):
    """Structured result for deferred execution tool."""

    success: bool
    tool_ids: list[str] = Field(default_factory=list)
    agent_id: str = ""
    content: str = ""
    error: str | None = None


class DeferredToolResolver:
    """Resolve top-k tool IDs by lightweight lexical matching."""

    def __init__(self, catalog_getter: Callable[[], list[ToolCatalogEntry]]) -> None:
        self._catalog_getter = catalog_getter

    def resolve(self, task: str, max_tools: int = 3) -> ToolResolutionResult:
        catalog = self._catalog_getter()
        if not catalog:
            return ToolResolutionResult(selected_tool_ids=[], candidates=[])

        task_terms = self._tokenize(task)
        if not task_terms:
            task_terms = set()

        scored: list[ResolvedToolCandidate] = []
        for entry in catalog:
            score = self._score_entry(entry, task_terms)
            scored.append(ResolvedToolCandidate(tool_id=entry.tool_id, score=score))

        scored.sort(key=lambda c: (-c.score, c.tool_id))
        top_matches = [c for c in scored if c.score > 0 and c.tool_id != "core_tools"]

        selected: list[str] = []
        if top_matches:
            selected.extend(c.tool_id for c in top_matches[: max(0, max_tools - 1)])

        has_core = any(c.tool_id == "core_tools" for c in scored)
        if has_core and (not selected or len(selected) < max_tools):
            selected.insert(0, "core_tools")

        selected = selected[: max_tools] if max_tools > 0 else []

        return ToolResolutionResult(selected_tool_ids=selected, candidates=scored)

    def _tokenize(self, text: str) -> set[str]:
        return {
            t.lower()
            for t in _TOKEN_RE.findall(text or "")
            if len(t) >= 3 and t.lower() not in _STOPWORDS
        }

    def _score_entry(self, entry: ToolCatalogEntry, task_terms: set[str]) -> int:
        if entry.tool_id == "core_tools":
            return 1

        score = 0
        id_terms = self._tokenize(entry.tool_id.replace("_", " "))
        name_terms = self._tokenize(entry.name)
        desc_terms = self._tokenize(entry.description)
        keyword_terms: set[str] = set()
        for kw in entry.keywords:
            keyword_terms.update(self._tokenize(kw))

        for term in task_terms:
            if term in id_terms:
                score += 7
            if term in name_terms:
                score += 5
            if term in keyword_terms:
                score += 4
            if term in desc_terms:
                score += 2

        return score


def make_deferred_tool_tools(
    factory: AgentFactory,
    registry: AgentRegistry,
    catalog_getter: Callable[[], list[ToolCatalogEntry]],
) -> list[Any]:
    """Create resolver and deferred execution gateway tools for Orchestrator."""

    resolver = DeferredToolResolver(catalog_getter=catalog_getter)

    @function_tool
    async def resolve_tools_for_task(
        task: str,
        max_tools: int = 3,
    ) -> ToolResolutionResult:
        """Resolve top tool IDs for a task.

        Use this before dynamic execution when you need visibility into selected tools.
        """
        return resolver.resolve(task=task, max_tools=max_tools)

    @function_tool
    async def run_with_resolved_tools(
        task: str,
        context: str = "",
        max_tools: int = 3,
        model: str | None = None,
        max_turns: int = 25,
    ) -> DeferredExecutionResult:
        """Execute task through a dynamic agent created with top-k resolved tools."""
        resolution = resolver.resolve(task=task, max_tools=max_tools)
        tool_ids = resolution.selected_tool_ids
        if not tool_ids:
            return DeferredExecutionResult(
                success=False,
                tool_ids=[],
                agent_id="",
                content="",
                error="No tools available for deferred execution",
            )

        try:
            spec = AgentSpec(
                name="Deferred Executor",
                description="Dynamic agent with task-scoped resolved tools",
                instruction=(
                    "You are a focused execution agent. "
                    "Use only the provided tools to complete the task. "
                    "Return concise, actionable output."
                ),
                tools=tool_ids,
                model=model,
                max_turns=max_turns,
            )
            agent_id = factory.create(spec)
            invocation_context: AgentInvocationContext | None = None
            if context:
                invocation_context = AgentInvocationContext(
                    conversation_summary=context,
                    user_message=None,
                    correlation_id=None,
                )
            response = await registry.invoke(agent_id, task, invocation_context)
            if response.status == "success":
                return DeferredExecutionResult(
                    success=True,
                    tool_ids=tool_ids,
                    agent_id=agent_id,
                    content=response.content,
                    error=None,
                )
            return DeferredExecutionResult(
                success=False,
                tool_ids=tool_ids,
                agent_id=agent_id,
                content=response.content,
                error=response.error or f"Agent returned status: {response.status}",
            )
        except Exception as exc:
            return DeferredExecutionResult(
                success=False,
                tool_ids=tool_ids,
                agent_id="",
                content="",
                error=str(exc),
            )

    return [resolve_tools_for_task, run_with_resolved_tools]


__all__ = [
    "DeferredExecutionResult",
    "DeferredToolResolver",
    "ResolvedToolCandidate",
    "ToolCatalogEntry",
    "ToolResolutionResult",
    "make_deferred_tool_tools",
]
