"""Tests for request_secure_input tool."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.events.topics import SystemTopics
from core.tools.secure_input import make_secure_input_tool


def _make_tool_ctx(tool_name: str, tool_arguments: str):
    from agents.tool_context import ToolContext

    return ToolContext(
        context=object(),
        tool_name=tool_name,
        tool_call_id="test-call-id",
        tool_arguments=tool_arguments,
    )


class TestMakeSecureInputTool:
    """make_secure_input_tool factory."""

    def test_returns_tool_with_correct_name(self) -> None:
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        assert getattr(tool, "name", None) == "request_secure_input"

    @pytest.mark.asyncio
    async def test_valid_secret_id_publishes_event(self) -> None:
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        args = json.dumps({
            "secret_id": "telegram_token",
            "prompt_message": "Enter bot token",
            "channel_id": "cli_channel",
        })
        result = await tool.on_invoke_tool(
            _make_tool_ctx(tool.name, args), args
        )
        event_bus.publish.assert_called_once()
        call_args = event_bus.publish.call_args
        assert call_args[0][0] == SystemTopics.SECURE_INPUT_REQUEST
        assert call_args[0][1] == "kernel"
        payload = call_args[0][2]
        assert payload["secret_id"] == "telegram_token"
        assert payload["prompt"] == "Enter bot token"
        assert payload["target_channel"] == "cli_channel"
        assert "Secure input request sent" in result
        assert "Do NOT ask" in result

    @pytest.mark.asyncio
    async def test_invalid_secret_id_returns_error_no_publish(self) -> None:
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        args = json.dumps({
            "secret_id": "invalid-id-with-dash",
            "prompt_message": "Enter token",
        })
        result = await tool.on_invoke_tool(
            _make_tool_ctx(tool.name, args), args
        )
        event_bus.publish.assert_not_called()
        assert "Error:" in result
        assert "invalid secret_id" in result

    @pytest.mark.asyncio
    async def test_secret_id_starting_with_number_rejected(self) -> None:
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        args = json.dumps({"secret_id": "123token", "prompt_message": "Enter"})
        result = await tool.on_invoke_tool(
            _make_tool_ctx(tool.name, args), args
        )
        event_bus.publish.assert_not_called()
        assert "Error:" in result

    @pytest.mark.asyncio
    async def test_default_channel_is_cli(self) -> None:
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        args = json.dumps({"secret_id": "my_secret", "prompt_message": "Enter value"})
        await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        payload = event_bus.publish.call_args[0][2]
        assert payload["target_channel"] == "cli_channel"
