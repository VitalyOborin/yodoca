"""Tests for request_secure_input tool."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.events.topics import SystemTopics
from core.tools.secure_input import _sanitize_secret_id, make_secure_input_tool


def _make_tool_ctx(tool_name: str, tool_arguments: str):
    from agents.tool_context import ToolContext

    return ToolContext(
        context=object(),
        tool_name=tool_name,
        tool_call_id="test-call-id",
        tool_arguments=tool_arguments,
    )


class TestSanitizeSecretId:
    """Unit tests for _sanitize_secret_id."""

    def test_already_valid(self) -> None:
        assert _sanitize_secret_id("telegram_token") == "telegram_token"

    def test_replaces_at_and_dot(self) -> None:
        assert _sanitize_secret_id("mail_app_password_v.oborin@auchan.ru") == (
            "mail_app_password_v_oborin_auchan_ru"
        )

    def test_replaces_dashes(self) -> None:
        assert _sanitize_secret_id("invalid-id-with-dash") == "invalid_id_with_dash"

    def test_strips_leading_digits(self) -> None:
        assert _sanitize_secret_id("123token") == "token"

    def test_collapses_consecutive_underscores(self) -> None:
        assert _sanitize_secret_id("a___b") == "a_b"

    def test_strips_trailing_underscores(self) -> None:
        assert _sanitize_secret_id("foo_") == "foo"

    def test_truncates_to_64_chars(self) -> None:
        long_id = "a" * 100
        assert len(_sanitize_secret_id(long_id)) == 64

    def test_empty_after_sanitize(self) -> None:
        assert _sanitize_secret_id("@@@") == ""

    def test_mixed_special_chars(self) -> None:
        assert _sanitize_secret_id("user+tag@example.com") == "user_tag_example_com"


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
        args = json.dumps(
            {
                "secret_id": "telegram_token",
                "prompt_message": "Enter bot token",
                "channel_id": "cli_channel",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        event_bus.publish.assert_called_once()
        call_args = event_bus.publish.call_args
        assert call_args[0][0] == SystemTopics.SECURE_INPUT_REQUEST
        assert call_args[0][1] == "kernel"
        payload = call_args[0][2]
        assert payload["secret_id"] == "telegram_token"
        assert payload["prompt"] == "Enter bot token"
        assert payload["target_channel"] == "cli_channel"
        assert result.success is True
        assert result.secret_id == "telegram_token"
        assert "Secure input request sent" in result.message
        assert "Do NOT ask" in result.message

    @pytest.mark.asyncio
    async def test_secret_id_with_special_chars_is_sanitized(self) -> None:
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        args = json.dumps(
            {
                "secret_id": "mail_app_password_v.oborin@auchan.ru",
                "prompt_message": "Enter App Password",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        event_bus.publish.assert_called_once()
        payload = event_bus.publish.call_args[0][2]
        assert payload["secret_id"] == "mail_app_password_v_oborin_auchan_ru"
        assert result.success is True
        assert result.secret_id == "mail_app_password_v_oborin_auchan_ru"

    @pytest.mark.asyncio
    async def test_dashes_are_sanitized(self) -> None:
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        args = json.dumps(
            {
                "secret_id": "invalid-id-with-dash",
                "prompt_message": "Enter token",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        event_bus.publish.assert_called_once()
        payload = event_bus.publish.call_args[0][2]
        assert payload["secret_id"] == "invalid_id_with_dash"
        assert result.success is True
        assert result.secret_id == "invalid_id_with_dash"

    @pytest.mark.asyncio
    async def test_leading_digits_stripped(self) -> None:
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        args = json.dumps({"secret_id": "123token", "prompt_message": "Enter"})
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        event_bus.publish.assert_called_once()
        assert result.success is True
        assert result.secret_id == "token"

    @pytest.mark.asyncio
    async def test_completely_invalid_id_returns_error(self) -> None:
        """An id that sanitizes to empty string still fails."""
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        args = json.dumps({"secret_id": "@@@", "prompt_message": "Enter"})
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        event_bus.publish.assert_not_called()
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_default_channel_is_cli(self) -> None:
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        tool = make_secure_input_tool(event_bus)
        args = json.dumps({"secret_id": "my_secret", "prompt_message": "Enter value"})
        await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        payload = event_bus.publish.call_args[0][2]
        assert payload["target_channel"] == "cli_channel"
