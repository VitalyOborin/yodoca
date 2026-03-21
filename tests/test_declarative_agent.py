"""Tests for declarative agent adapter wiring."""

from unittest.mock import MagicMock, patch

import pytest

from core.extensions.declarative_agent import DeclarativeAgentAdapter
from core.extensions.manifest import ExtensionManifest


@pytest.mark.asyncio
async def test_initialize_sets_model_settings_from_manifest() -> None:
    manifest = ExtensionManifest.model_validate(
        {
            "id": "agent_ext",
            "name": "Agent Ext",
            "description": "Test declarative agent",
            "agent": {
                "integration_mode": "tool",
                "model": "gpt-5-mini",
                "parallel_tool_calls": True,
            },
        }
    )
    adapter = DeclarativeAgentAdapter(manifest)
    context = MagicMock()
    context.model_router = MagicMock()
    context.agent_id = "agent_ext"
    context.model_router.get_model.return_value = "resolved-model"
    context.agent_model = ""
    context.resolved_instructions = "Follow instruction"
    context.resolved_tools = ["tool-1"]

    with patch("core.extensions.declarative_agent.Agent") as agent_cls:
        await adapter.initialize(context)

    kwargs = agent_cls.call_args.kwargs
    assert kwargs["name"] == "Agent Ext"
    assert kwargs["model"] == "resolved-model"
    assert kwargs["tools"] == ["tool-1"]
    assert kwargs["model_settings"].parallel_tool_calls is True
