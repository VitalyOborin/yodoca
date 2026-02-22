"""Tests for channel tools: list_channels, send_to_channel."""

import json
import pytest

from core.extensions.contract import ChannelProvider
from core.extensions.router import MessageRouter
from core.tools.channel import make_channel_tools


class MockChannel(ChannelProvider):
    """Channel that records proactive messages."""

    def __init__(self) -> None:
        self.proactive_sent: list[str] = []

    async def send_to_user(self, user_id: str, message: str) -> None:
        pass

    async def send_message(self, message: str) -> None:
        self.proactive_sent.append(message)


def _get_tool_by_name(tools: list, name: str):
    """Get tool by name from make_channel_tools result."""
    for t in tools:
        if getattr(t, "name", None) == name:
            return t
    return None


class TestMakeChannelTools:
    """make_channel_tools factory."""

    def test_returns_two_tools(self) -> None:
        router = MessageRouter()
        tools = make_channel_tools(router)
        assert len(tools) == 2
        assert _get_tool_by_name(tools, "list_channels") is not None
        assert _get_tool_by_name(tools, "send_to_channel") is not None


class TestListChannelsLogic:
    """list_channels logic via router (tools are thin wrappers)."""

    def test_list_channels_empty(self) -> None:
        router = MessageRouter()
        ids = router.get_channel_ids()
        assert ids == []
        descriptions = router.get_channel_descriptions()
        assert descriptions == {}

    def test_list_channels_with_descriptions(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("cli_channel", ch)
        router.register_channel("telegram_channel", ch)
        router.set_channel_descriptions({
            "cli_channel": "CLI Channel",
            "telegram_channel": "Telegram Channel",
        })
        ids = router.get_channel_ids()
        assert "cli_channel" in ids
        assert "telegram_channel" in ids
        descriptions = router.get_channel_descriptions()
        assert descriptions["cli_channel"] == "CLI Channel"
        assert descriptions["telegram_channel"] == "Telegram Channel"


class TestSendToChannelLogic:
    """send_to_channel logic: notify_user with validation."""

    @pytest.mark.asyncio
    async def test_notify_user_valid_channel(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("telegram_channel", ch)
        await router.notify_user("Hello!", "telegram_channel")
        assert ch.proactive_sent == ["Hello!"]

    @pytest.mark.asyncio
    async def test_notify_user_invalid_channel_falls_back_to_first(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("cli_channel", ch)
        await router.notify_user("Hello!", "unknown_channel")
        assert ch.proactive_sent == ["Hello!"]


def _make_tool_ctx(tool_name: str, tool_arguments: str):
    """Create minimal ToolContext for testing."""
    from agents.tool_context import ToolContext

    return ToolContext(
        context=object(),
        tool_name=tool_name,
        tool_call_id="test-call-id",
        tool_arguments=tool_arguments,
    )


class TestListChannelsToolOutput:
    """list_channels returns typed JSON array."""

    @pytest.mark.asyncio
    async def test_empty_returns_empty_array(self) -> None:
        router = MessageRouter()
        tools = make_channel_tools(router)
        list_tool = _get_tool_by_name(tools, "list_channels")
        result = await list_tool.on_invoke_tool(
            _make_tool_ctx(list_tool.name, "{}"), "{}"
        )
        data = json.loads(result)
        assert data == []

    @pytest.mark.asyncio
    async def test_returns_channel_id_and_description(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("cli_channel", ch)
        router.register_channel("telegram_channel", ch)
        router.set_channel_descriptions({
            "cli_channel": "CLI Channel",
            "telegram_channel": "Telegram Channel",
        })
        tools = make_channel_tools(router)
        list_tool = _get_tool_by_name(tools, "list_channels")
        result = await list_tool.on_invoke_tool(
            _make_tool_ctx(list_tool.name, "{}"), "{}"
        )
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["channel_id"] == "cli_channel"
        assert data[0]["description"] == "CLI Channel"
        assert data[1]["channel_id"] == "telegram_channel"
        assert data[1]["description"] == "Telegram Channel"


class TestSendToChannelToolOutput:
    """send_to_channel returns typed JSON with success/error."""

    @pytest.mark.asyncio
    async def test_success_returns_success_true(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("telegram_channel", ch)
        tools = make_channel_tools(router)
        send_tool = _get_tool_by_name(tools, "send_to_channel")
        args = json.dumps({"channel_id": "telegram_channel", "text": "Hi"})
        result = await send_tool.on_invoke_tool(
            _make_tool_ctx(send_tool.name, args), args
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "error" not in data

    @pytest.mark.asyncio
    async def test_invalid_channel_returns_success_false_and_error(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("cli_channel", ch)
        tools = make_channel_tools(router)
        send_tool = _get_tool_by_name(tools, "send_to_channel")
        args = json.dumps({"channel_id": "unknown_channel", "text": "Hi"})
        result = await send_tool.on_invoke_tool(
            _make_tool_ctx(send_tool.name, args), args
        )
        data = json.loads(result)
        assert data["success"] is False
        assert "error" in data
        assert "not found" in data["error"].lower()
