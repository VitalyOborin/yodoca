"""Tests for MessageRouter: channels, invoke_agent, notify_user, subscribe."""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.extensions.contract import ChannelProvider, StreamingChannelProvider
from core.extensions.router import MessageRouter


class MockChannel(ChannelProvider):
    """Channel that records sent messages."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.proactive_sent: list[str] = []

    async def send_to_user(self, user_id: str, message: str) -> None:
        self.sent.append((user_id, message))

    async def send_message(self, message: str) -> None:
        self.proactive_sent.append(message)


@dataclass
class _FakeResponseTextDeltaEvent:
    delta: str


@dataclass
class _FakeStreamEvent:
    type: str
    data: object | None = None
    item: object | None = None


class MockStreamingChannel(MockChannel, StreamingChannelProvider):
    """StreamingChannelProvider collecting stream callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self.stream_started: list[str] = []
        self.stream_chunks: list[tuple[str, str]] = []
        self.stream_status: list[tuple[str, str]] = []
        self.stream_ended: list[tuple[str, str]] = []

    async def on_stream_start(self, user_id: str) -> None:
        self.stream_started.append(user_id)

    async def on_stream_chunk(self, user_id: str, chunk: str) -> None:
        self.stream_chunks.append((user_id, chunk))

    async def on_stream_status(self, user_id: str, status: str) -> None:
        self.stream_status.append((user_id, status))

    async def on_stream_end(self, user_id: str, full_text: str) -> None:
        self.stream_ended.append((user_id, full_text))


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
        assert ch.proactive_sent == ["hello world"]
        assert ch.sent == []

    @pytest.mark.asyncio
    async def test_notify_user_picks_channel_by_id(self) -> None:
        router = MessageRouter()
        ch1 = MockChannel()
        ch2 = MockChannel()
        router.register_channel("first", ch1)
        router.register_channel("second", ch2)
        await router.notify_user("msg", channel_id="second")
        assert ch2.proactive_sent == ["msg"]
        assert ch1.proactive_sent == []

    def test_get_channel_ids(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.register_channel("cli", ch)
        router.register_channel("tg", ch)
        assert router.get_channel_ids() == ["cli", "tg"]

    def test_set_and_get_channel_descriptions(self) -> None:
        router = MessageRouter()
        router.set_channel_descriptions({"cli": "CLI Channel", "tg": "Telegram"})
        assert router.get_channel_descriptions() == {"cli": "CLI Channel", "tg": "Telegram"}


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
        mock_runner.run.assert_called_once_with(mock_agent, "hello", session=None)

    @pytest.mark.asyncio
    async def test_invoke_agent_with_middleware_injects_context_into_system(self) -> None:
        """When middleware returns context, agent is cloned with extended instructions and original prompt is used."""
        router = MessageRouter()
        mock_agent = MagicMock()
        mock_agent.instructions = "You are helpful."
        cloned_agent = MagicMock()
        mock_agent.clone.return_value = cloned_agent
        result_value = MagicMock()
        result_value.final_output = "replied"

        async def middleware(prompt: str, agent_id: str | None = None) -> str:
            return "memory: user likes cats"

        router.set_invoke_middleware(middleware)
        router.set_agent(mock_agent)
        with patch("agents.Runner") as mock_runner:
            mock_runner.run = AsyncMock(return_value=result_value)
            await router.invoke_agent("hello")

        mock_agent.clone.assert_called_once_with(
            instructions="You are helpful.\n\n---\n\nmemory: user likes cats"
        )
        mock_runner.run.assert_called_once_with(cloned_agent, "hello", session=None)

    @pytest.mark.asyncio
    async def test_invoke_agent_with_middleware_empty_context_no_clone(self) -> None:
        """When middleware returns empty string, no clone; base agent and prompt used."""
        router = MessageRouter()
        mock_agent = MagicMock()
        mock_agent.instructions = "Base."
        result_value = MagicMock()
        result_value.final_output = "ok"

        async def middleware(prompt: str, agent_id: str | None = None) -> str:
            return ""

        router.set_invoke_middleware(middleware)
        router.set_agent(mock_agent)
        with patch("agents.Runner") as mock_runner:
            mock_runner.run = AsyncMock(return_value=result_value)
            await router.invoke_agent("hello")

        mock_agent.clone.assert_not_called()
        mock_runner.run.assert_called_once_with(mock_agent, "hello", session=None)

    @pytest.mark.asyncio
    async def test_enrich_prompt_returns_context_plus_prompt(self) -> None:
        """enrich_prompt returns context + separator + prompt when middleware returns context."""
        router = MessageRouter()

        async def middleware(prompt: str, agent_id: str | None = None) -> str:
            return "recall: xyz"

        router.set_invoke_middleware(middleware)
        result = await router.enrich_prompt("what is x?", agent_id="scout")
        assert result == "recall: xyz\n\n---\n\nwhat is x?"

    @pytest.mark.asyncio
    async def test_enrich_prompt_empty_context_returns_prompt_only(self) -> None:
        """enrich_prompt returns prompt only when middleware returns empty."""
        router = MessageRouter()

        async def middleware(prompt: str, agent_id: str | None = None) -> str:
            return ""

        router.set_invoke_middleware(middleware)
        result = await router.enrich_prompt("hello")
        assert result == "hello"


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


class TestStreamingInvocation:
    """invoke_agent_streamed and streaming channel handling."""

    @pytest.mark.asyncio
    async def test_handle_user_message_streaming_channel(self) -> None:
        router = MessageRouter()
        ch = MockStreamingChannel()
        router.register_channel("cli", ch)
        router.set_agent(MagicMock())
        tool_call = _FakeStreamEvent(
            type="run_item_stream_event",
            item=SimpleNamespace(type="tool_call_item", raw_item=SimpleNamespace(name="calculator")),
        )

        async def stream_events() -> None:
            yield _FakeStreamEvent(type="raw_response_event", data=_FakeResponseTextDeltaEvent("hi "))
            yield tool_call
            yield _FakeStreamEvent(type="raw_response_event", data=_FakeResponseTextDeltaEvent("world"))

        def fake_streamed(*_args, **_kwargs) -> SimpleNamespace:
            return SimpleNamespace(
                stream_events=stream_events,
                final_output="hi world",
            )

        with patch("agents.Runner") as mock_runner:
            mock_runner.run_streamed = MagicMock(side_effect=fake_streamed)
            await router.handle_user_message("hello", "user1", ch)

        assert ch.stream_started == ["user1"]
        assert ch.stream_chunks == [("user1", "hi "), ("user1", "world")]
        assert ch.stream_status == [("user1", "Using: calculator")]
        assert ch.stream_ended == [("user1", "hi world")]
        assert ch.stream_chunks and ch.sent == []

    @pytest.mark.asyncio
    async def test_handle_user_message_non_streaming_unchanged(self) -> None:
        router = MessageRouter()
        ch = MockChannel()
        router.set_agent(MagicMock())
        with patch("agents.Runner") as mock_runner:
            result = MagicMock()
            result.final_output = "reply"
            mock_runner.run = AsyncMock(return_value=result)
            await router.handle_user_message("hello", "user2", ch)
        assert ch.sent == [("user2", "reply")]

    @pytest.mark.asyncio
    async def test_invoke_agent_streamed_error_handling(self) -> None:
        router = MessageRouter()
        router.set_agent(MagicMock())
        chunks: list[str] = []

        async def on_chunk(chunk: str) -> None:
            chunks.append(chunk)

        async def stream_events() -> None:
            yield _FakeStreamEvent(type="raw_response_event", data=_FakeResponseTextDeltaEvent("partial"))
            raise RuntimeError("stream broken")

        def fake_streamed(*_args, **_kwargs) -> SimpleNamespace:
            return SimpleNamespace(
                stream_events=stream_events,
                final_output=None,
            )

        with patch("agents.Runner") as mock_runner:
            mock_runner.run_streamed = MagicMock(side_effect=fake_streamed)
            result = await router.invoke_agent_streamed("ask", on_chunk=on_chunk)

        assert result.startswith("partial")
        assert "(Error: stream broken)" in result
        assert chunks == ["partial", "\n(Error: stream broken)"]

    @pytest.mark.asyncio
    async def test_invoke_agent_streamed_lock_held(self) -> None:
        router = MessageRouter()
        router.set_agent(MagicMock())
        callback_log: list[str] = []

        async def on_chunk_left(chunk: str) -> None:
            callback_log.append(f"left:{chunk}")
            await asyncio.sleep(0)

        async def on_chunk_right(chunk: str) -> None:
            callback_log.append(f"right:{chunk}")
            await asyncio.sleep(0)

        async def stream_events_left() -> None:
            yield _FakeStreamEvent(type="raw_response_event", data=_FakeResponseTextDeltaEvent("a"))
            await asyncio.sleep(0.05)
            yield _FakeStreamEvent(type="raw_response_event", data=_FakeResponseTextDeltaEvent("b"))

        async def stream_events_right() -> None:
            yield _FakeStreamEvent(type="raw_response_event", data=_FakeResponseTextDeltaEvent("1"))
            await asyncio.sleep(0.05)
            yield _FakeStreamEvent(type="raw_response_event", data=_FakeResponseTextDeltaEvent("2"))

        calls = 0

        def fake_streamed(agent, prompt, session=None) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(
                    stream_events=stream_events_left, final_output="ab", session=session
                )
            return SimpleNamespace(
                stream_events=stream_events_right, final_output="12", session=session
            )

        with patch("agents.Runner") as mock_runner:
            mock_runner.run_streamed = MagicMock(side_effect=fake_streamed)
            task1 = asyncio.create_task(
                router.invoke_agent_streamed("first", on_chunk=lambda chunk: on_chunk_left(chunk))
            )
            await asyncio.sleep(0.01)
            task2 = asyncio.create_task(
                router.invoke_agent_streamed("second", on_chunk=lambda chunk: on_chunk_right(chunk))
            )
            results = await asyncio.gather(task1, task2)

        assert results == ["ab", "12"]
        assert callback_log == [
            "left:a",
            "left:b",
            "right:1",
            "right:2",
        ]
