"""Tests for channel tools: list_channels, send_to_channel."""

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
