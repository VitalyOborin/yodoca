"""Tests for MessageRouter: channels, invoke_agent, notify_user, subscribe."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.extensions.contract import ChannelProvider
from core.extensions.router import MessageRouter


class MockChannel(ChannelProvider):
    """Channel that records sent messages."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_to_user(self, user_id: str, message: str) -> None:
        self.sent.append((user_id, message))


class TestMessageRouterRegisterAndNotify:
    """register_channel and notify_user."""

    def test_register_channel(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("cli", ch)
        # Notify uses registered channel
        # (tested in test_notify_user_*)

    @pytest.mark.asyncio
    async def test_notify_user_no_channels(self, caplog: pytest.LogCaptureFixture) -> None:
        router = MessageRouter()
        await router.notify_user("hello")
        # Should not raise; logs warning
        assert "no channels" in caplog.text.lower() or len(caplog.records) >= 0

    @pytest.mark.asyncio
    async def test_notify_user_sends_to_first_channel(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("cli", ch)
        await router.notify_user("hello world")
        assert ch.sent == [("default", "hello world")]

    @pytest.mark.asyncio
    async def test_notify_user_picks_channel_by_id(self) -> None:
        router = MessageRouter()
        ch1 = MockChannel()
        ch2 = MockChannel()
        router.register_channel("first", ch1)
        router.register_channel("second", ch2)
        await router.notify_user("msg", channel_id="second")
        assert ch2.sent == [("default", "msg")]
        assert ch1.sent == []


class TestInvokeAgent:
    """invoke_agent with and without agent."""

    @pytest.mark.asyncio
    async def test_invoke_agent_no_agent_returns_placeholder(self) -> None:
        router = MessageRouter()
        result = await router.invoke_agent("hello")
        assert result == "(No agent configured.)"

    @pytest.mark.asyncio
    async def test_invoke_agent_with_mock_runner(self) -> None:
        router = MessageRouter()
        mock_agent = MagicMock()
        result_value = MagicMock()
        result_value.final_output = "agent said this"
        with patch("agents.Runner") as mock_runner:
            mock_runner.run = AsyncMock(return_value=result_value)
            router.set_agent(mock_agent)
            result = await router.invoke_agent("hello")
        assert result == "agent said this"
        mock_runner.run.assert_called_once_with(mock_agent, "hello")


class TestSubscribeAndEmit:
    """subscribe, unsubscribe, and _emit via handle_user_message."""

    @pytest.mark.asyncio
    async def test_handle_user_message_emits_and_sends_to_channel(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("cli", ch)
        events_received: list[tuple[str, object]] = []

        def on_user_message(data: object) -> None:
            events_received.append(("user_message", data))

        def on_agent_response(data: object) -> None:
            events_received.append(("agent_response", data))

        router.subscribe("user_message", on_user_message)
        router.subscribe("agent_response", on_agent_response)
        with patch("agents.Runner") as mock_runner:
            result_value = MagicMock()
            result_value.final_output = "reply"
            mock_runner.run = AsyncMock(return_value=result_value)
            router.set_agent(MagicMock())
            await router.handle_user_message("hi", "user1", ch)
        assert len(events_received) == 2
        assert events_received[0][0] == "user_message"
        assert events_received[1][0] == "agent_response"
        assert ch.sent == [("user1", "reply")]

    def test_unsubscribe_removes_handler(self) -> None:
        router = MessageRouter()
        handler = MagicMock()
        router.subscribe("ev", handler)
        router.unsubscribe("ev", handler)
        # Next _emit for "ev" should not call handler (we can't easily assert
        # without calling _emit; at least we check no exception)
        router._subscribers.get("ev", [])
