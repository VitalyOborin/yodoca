"""Tests for SemanticAgentSelector."""

from pathlib import Path

import pytest

from core.agents.semantic_agent_selector import AgentProfile, SemanticAgentSelector


def _catalog() -> list[AgentProfile]:
    return [
        AgentProfile(
            agent_id="memory_agent",
            name="Memory Agent",
            description="Handles recall, long-term context, and memory search",
            tools=["memory"],
            sample_queries=[
                "remember this preference",
                "what did we discuss last week",
            ],
        ),
        AgentProfile(
            agent_id="code_agent",
            name="Code Agent",
            description="Writes and reviews Python code",
            tools=["core_tools"],
            sample_queries=[
                "refactor this function",
                "write tests for this module",
            ],
        ),
    ]


async def _fake_embed_batch(texts: list[str]) -> list[list[float] | None]:
    out: list[list[float] | None] = []
    for t in texts:
        q = t.lower()
        if "memory" in q or "remember" in q or "discuss" in q:
            out.append([1.0, 0.0])
        elif "code" in q or "python" in q or "tests" in q:
            out.append([0.0, 1.0])
        else:
            out.append([0.1, 0.1])
    return out


class TestSemanticAgentSelector:
    @pytest.mark.asyncio
    async def test_select_agents_semantic_prefers_memory(
        self, tmp_path: Path
    ) -> None:
        selector = SemanticAgentSelector(
            catalog_getter=_catalog,
            db_path=tmp_path / "agent_selector.db",
            embed_batch=_fake_embed_batch,
        )
        result = await selector.select_agents(
            task="Please remember my timezone and recall it tomorrow",
            top_k=2,
        )
        assert result.strategy == "semantic"
        assert result.selected_agent_ids[0] == "memory_agent"

    @pytest.mark.asyncio
    async def test_select_agents_lexical_fallback(self, tmp_path: Path) -> None:
        selector = SemanticAgentSelector(
            catalog_getter=_catalog,
            db_path=tmp_path / "agent_selector.db",
            embed_batch=None,
        )
        result = await selector.select_agents(
            task="write tests for my code",
            top_k=2,
        )
        assert result.strategy == "lexical"
        assert result.selected_agent_ids[0] == "code_agent"

