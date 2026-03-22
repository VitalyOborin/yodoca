"""Tests for Memory write-path agent (Phase 3)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_memory_ext = (
    Path(__file__).resolve().parent.parent / "sandbox" / "extensions" / "memory"
)
sys.path.insert(0, str(_memory_ext))

from agent import ConsolidationResult, MemoryAgent, create_memory_agent


class TestMemoryAgent:
    """MemoryAgent thread consolidation with mocked Runner.run."""

    @pytest.mark.asyncio
    async def test_consolidate_thread_returns_completed(
        self,
    ) -> None:
        tools = []
        instructions = "Consolidate thread."
        agent = MemoryAgent(model=None, tools=tools, instructions=instructions)

        with patch("agent.Runner") as mock_runner:
            mock_runner.run = AsyncMock()
            result = await agent.consolidate_thread("thread-123")

        assert isinstance(result, ConsolidationResult)
        assert result.thread_id == "thread-123"
        assert result.status == "completed"
        mock_runner.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_consolidate_thread_returns_error_on_exception(
        self,
    ) -> None:
        tools = []
        instructions = "Consolidate thread."
        agent = MemoryAgent(model=None, tools=tools, instructions=instructions)

        with patch("agent.Runner") as mock_runner:
            mock_runner.run = AsyncMock(side_effect=RuntimeError("model failed"))
            result = await agent.consolidate_thread("thread-456")

        assert isinstance(result, ConsolidationResult)
        assert result.thread_id == "thread-456"
        assert result.status == "error"


class TestCreateMemoryAgent:
    """create_memory_agent factory loads instructions from prompt.jinja2."""

    @pytest.mark.asyncio
    async def test_create_memory_agent_returns_agent(
        self,
    ) -> None:
        ext_dir = _memory_ext
        tools = []
        agent = create_memory_agent(model=None, tools=tools, extension_dir=ext_dir)

        assert agent is not None
        assert hasattr(agent, "consolidate_thread")
