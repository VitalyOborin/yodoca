"""Tests for configure_extension tool."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.extensions.contract import SetupProvider
from core.tools.configure_extension import make_configure_extension_tool


def _make_tool_ctx(tool_name: str, tool_arguments: str):
    from agents.tool_context import ToolContext

    return ToolContext(
        context=object(),
        tool_name=tool_name,
        tool_call_id="test-call-id",
        tool_arguments=tool_arguments,
    )


class TestMakeConfigureExtensionTool:
    """make_configure_extension_tool factory."""

    def test_returns_tool_with_correct_name(self) -> None:
        tool = make_configure_extension_tool({})
        assert getattr(tool, "name", None) == "configure_extension"

    @pytest.mark.asyncio
    async def test_extension_not_found_returns_error(self) -> None:
        tool = make_configure_extension_tool({})
        args = json.dumps(
            {
                "extension_id": "nonexistent",
                "param_name": "token",
                "value": "secret",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        assert result.success is False
        assert result.error is not None
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_non_setup_provider_returns_error(self) -> None:
        tool = make_configure_extension_tool({"some_ext": object()})
        args = json.dumps(
            {
                "extension_id": "some_ext",
                "param_name": "token",
                "value": "secret",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        assert result.success is False
        assert result.error is not None
        assert "not a SetupProvider" in result.error

    @pytest.mark.asyncio
    async def test_apply_config_error_returns_error(self) -> None:
        mock_ext = MagicMock(spec=SetupProvider)
        mock_ext.apply_config = AsyncMock(side_effect=ValueError("Invalid value"))
        mock_ext.on_setup_complete = AsyncMock(return_value=(False, "token required"))

        tool = make_configure_extension_tool({"my_ext": mock_ext})
        args = json.dumps(
            {
                "extension_id": "my_ext",
                "param_name": "token",
                "value": "",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        assert result.success is False
        assert result.error is not None
        assert "apply_config failed" in result.error or "Invalid value" in result.error

    @pytest.mark.asyncio
    async def test_on_setup_complete_failure_returns_error(self) -> None:
        mock_ext = MagicMock(spec=SetupProvider)
        mock_ext.apply_config = AsyncMock()
        mock_ext.on_setup_complete = AsyncMock(return_value=(False, "token required"))

        tool = make_configure_extension_tool({"my_ext": mock_ext})
        args = json.dumps(
            {
                "extension_id": "my_ext",
                "param_name": "token",
                "value": "bad-token",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        assert result.success is False
        assert result.error == "token required"

    @pytest.mark.asyncio
    async def test_success_returns_message(self) -> None:
        mock_ext = MagicMock(spec=SetupProvider)
        mock_ext.apply_config = AsyncMock()
        mock_ext.on_setup_complete = AsyncMock(
            return_value=(True, "Telegram connected: @mybot")
        )

        tool = make_configure_extension_tool({"telegram_channel": mock_ext})
        args = json.dumps(
            {
                "extension_id": "telegram_channel",
                "param_name": "token",
                "value": "valid-token",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        assert result.success is True
        assert result.message == "Telegram connected: @mybot"
        assert result.error is None
        mock_ext.apply_config.assert_called_once_with("token", "valid-token")
        mock_ext.on_setup_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_secret_param_resolved_via_secret_resolver(self) -> None:
        """When value is a secret_id, the tool resolves it to the real secret."""
        real_token = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
        mock_ext = MagicMock(spec=SetupProvider)
        mock_ext.get_setup_schema.return_value = [
            {
                "name": "token",
                "description": "Bot token",
                "secret": True,
                "required": True,
            },
        ]
        mock_ext.apply_config = AsyncMock()
        mock_ext.on_setup_complete = AsyncMock(
            return_value=(True, "Telegram connected: @mybot")
        )

        async def fake_resolver(name: str) -> str | None:
            return real_token if name == "telegram_bot_token" else None

        tool = make_configure_extension_tool(
            {"telegram_channel": mock_ext},
            secret_resolver=fake_resolver,
        )
        args = json.dumps(
            {
                "extension_id": "telegram_channel",
                "param_name": "token",
                "value": "telegram_bot_token",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        assert result.success is True
        mock_ext.apply_config.assert_called_once_with("token", real_token)

    @pytest.mark.asyncio
    async def test_non_secret_param_not_resolved(self) -> None:
        """Non-secret params are passed through without resolution."""
        mock_ext = MagicMock(spec=SetupProvider)
        mock_ext.get_setup_schema.return_value = [
            {
                "name": "chat_id",
                "description": "Chat ID",
                "secret": False,
                "required": False,
            },
        ]
        mock_ext.apply_config = AsyncMock()
        mock_ext.on_setup_complete = AsyncMock(return_value=(True, "OK"))

        resolver_called = False

        async def fake_resolver(name: str) -> str | None:
            nonlocal resolver_called
            resolver_called = True
            return "should-not-be-used"

        tool = make_configure_extension_tool(
            {"my_ext": mock_ext},
            secret_resolver=fake_resolver,
        )
        args = json.dumps(
            {
                "extension_id": "my_ext",
                "param_name": "chat_id",
                "value": "12345",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        assert result.success is True
        assert not resolver_called
        mock_ext.apply_config.assert_called_once_with("chat_id", "12345")

    @pytest.mark.asyncio
    async def test_secret_resolver_returns_none_uses_original_value(self) -> None:
        """If resolver can't find the secret, use original value (direct secret)."""
        mock_ext = MagicMock(spec=SetupProvider)
        mock_ext.get_setup_schema.return_value = [
            {
                "name": "token",
                "description": "API key",
                "secret": True,
                "required": True,
            },
        ]
        mock_ext.apply_config = AsyncMock()
        mock_ext.on_setup_complete = AsyncMock(return_value=(True, "OK"))

        async def fake_resolver(name: str) -> str | None:
            return None

        tool = make_configure_extension_tool(
            {"my_ext": mock_ext},
            secret_resolver=fake_resolver,
        )
        args = json.dumps(
            {
                "extension_id": "my_ext",
                "param_name": "token",
                "value": "actual-api-key-value",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        assert result.success is True
        mock_ext.apply_config.assert_called_once_with("token", "actual-api-key-value")

    @pytest.mark.asyncio
    async def test_secret_id_waits_until_secret_is_available(self) -> None:
        """For secret_id references, tool waits briefly for secure input to persist."""
        mock_ext = MagicMock(spec=SetupProvider)
        mock_ext.get_setup_schema.return_value = [
            {
                "name": "token",
                "description": "API key",
                "secret": True,
                "required": True,
            },
        ]
        mock_ext.apply_config = AsyncMock()
        mock_ext.on_setup_complete = AsyncMock(return_value=(True, "OK"))

        calls = 0

        async def fake_resolver(name: str) -> str | None:
            nonlocal calls
            calls += 1
            return "resolved-token" if calls >= 3 else None

        tool = make_configure_extension_tool(
            {"my_ext": mock_ext},
            secret_resolver=fake_resolver,
        )
        args = json.dumps(
            {
                "extension_id": "my_ext",
                "param_name": "token",
                "value": "telegram_bot_token",
            }
        )
        result = await tool.on_invoke_tool(_make_tool_ctx(tool.name, args), args)
        assert result.success is True
        assert calls >= 3
        mock_ext.apply_config.assert_called_once_with("token", "resolved-token")
