"""Direct unit tests for router subcomponents after MessageRouter decomposition."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.extensions.contract import TurnContext
from core.extensions.persistence.thread_manager import ThreadManager
from core.extensions.routing.approval_coordinator import ApprovalCoordinator
from core.extensions.routing.response_delivery import ResponseDeliveryService


class _StreamingChannel:
    def __init__(self) -> None:
        self.on_stream_start = AsyncMock()
        self.on_stream_chunk = AsyncMock()
        self.on_stream_status = AsyncMock()
        self.on_stream_end = AsyncMock()
        self.send_to_user = AsyncMock()
        self.send_message = AsyncMock()


class _PlainChannel:
    def __init__(self) -> None:
        self.send_to_user = AsyncMock()
        self.send_message = AsyncMock()


class TestThreadManager:
    @pytest.mark.asyncio
    async def test_maybe_rotate_rotates_and_publishes(self, tmp_path) -> None:
        manager = ThreadManager()
        event_bus = MagicMock()
        event_bus.publish = AsyncMock(return_value=1)
        manager.configure_thread(
            thread_db_path=str(tmp_path / "thread.db"),
            thread_timeout=1,
            event_bus=event_bus,
            now_ts=1000.0,
        )
        old_id = manager.thread_id
        manager._last_message_at = 1000.0

        await manager.maybe_rotate(now_ts=1002.0)

        assert manager.thread_id != old_id
        event_bus.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_last_active_at_is_integer_and_stable(self, tmp_path) -> None:
        manager = ThreadManager()
        db_path = tmp_path / "thread.db"
        manager.configure_thread(
            thread_db_path=str(db_path),
            thread_timeout=1800,
            event_bus=None,
            now_ts=1000.0,
        )
        thread_store = manager.get_or_create_thread("sess01", "cli")
        await thread_store.add_items([{"role": "user", "content": "Hello"}])

        first = await manager.sync_last_active_at("sess01")
        second = await manager.sync_last_active_at("sess01")
        assert first is not None and second is not None
        assert isinstance(first, int)
        assert first == second

    @pytest.mark.asyncio
    async def test_sync_last_active_at_changes_after_new_message(
        self, tmp_path
    ) -> None:
        manager = ThreadManager()
        db_path = tmp_path / "thread.db"
        manager.configure_thread(
            thread_db_path=str(db_path),
            thread_timeout=1800,
            event_bus=None,
            now_ts=1000.0,
        )
        thread_store = manager.get_or_create_thread("sess01", "cli")
        await thread_store.add_items([{"role": "user", "content": "Hello"}])
        before = await manager.sync_last_active_at("sess01")
        assert before is not None

        same = await manager.sync_last_active_at("sess01")
        assert same == before

        await asyncio.sleep(1.1)
        await thread_store.add_items([{"role": "assistant", "content": "Hi"}])
        after = await manager.sync_last_active_at("sess01")
        assert after is not None
        assert after > before

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT updated_at FROM threads WHERE thread_id = ?",
                ("sess01",),
            ).fetchone()
        assert row is not None
        assert isinstance(row[0], (int, str))


class TestApprovalCoordinator:
    @pytest.mark.asyncio
    async def test_run_with_approval_loop_single_run_without_interruptions(
        self,
    ) -> None:
        coordinator = ApprovalCoordinator()
        result = SimpleNamespace(final_output="ok", interruptions=None)

        with patch("agents.Runner") as mock_runner:
            mock_runner.run = AsyncMock(return_value=result)
            out = await coordinator.run_with_approval_loop(
                agent=MagicMock(),
                input_or_state="hello",
                session=None,
                channel_id=None,
            )

        assert out is result
        mock_runner.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_with_approval_loop_handles_interruptions(self) -> None:
        coordinator = ApprovalCoordinator()
        state = MagicMock()
        result_1 = SimpleNamespace(
            interruptions=[SimpleNamespace(name="danger_tool", arguments="{}")],
            to_state=MagicMock(return_value=state),
        )
        result_2 = SimpleNamespace(final_output="done", interruptions=[])

        with patch("agents.Runner") as mock_runner:
            mock_runner.run = AsyncMock(side_effect=[result_1, result_2])
            out = await coordinator.run_with_approval_loop(
                agent=MagicMock(),
                input_or_state="hello",
                session=None,
                channel_id="cli",
            )

        assert out is result_2
        assert mock_runner.run.await_count == 2
        state.reject.assert_called_once()


class TestResponseDeliveryService:
    @pytest.mark.asyncio
    async def test_deliver_non_streaming_uses_send_to_user(self) -> None:
        invoker = MagicMock()
        invoker.invoke_agent = AsyncMock(return_value="reply")
        service = ResponseDeliveryService(invoker=invoker)
        channel = _PlainChannel()

        result = await service.deliver(
            channel=channel,
            user_id="u1",
            text="hello",
            turn_context=TurnContext(agent_id="orchestrator"),
        )

        assert result == "reply"
        invoker.invoke_agent.assert_awaited_once()
        channel.send_to_user.assert_awaited_once_with("u1", "reply")

    @pytest.mark.asyncio
    async def test_deliver_streaming_uses_stream_callbacks(self) -> None:
        async def _streamed(
            _prompt: str,
            on_chunk,
            on_tool_call,
            turn_context: TurnContext | None = None,
            session=None,
        ) -> str:
            await on_chunk("hi ")
            await on_tool_call("calculator")
            await on_chunk("world")
            return "hi world"

        invoker = MagicMock()
        invoker.invoke_agent_streamed = AsyncMock(side_effect=_streamed)
        service = ResponseDeliveryService(invoker=invoker)
        channel = _StreamingChannel()

        result = await service.deliver(
            channel=channel,
            user_id="u1",
            text="hello",
            turn_context=TurnContext(agent_id="orchestrator"),
        )

        assert result == "hi world"
        channel.on_stream_start.assert_awaited_once_with("u1")
        channel.on_stream_chunk.assert_any_await("u1", "hi ")
        channel.on_stream_chunk.assert_any_await("u1", "world")
        channel.on_stream_status.assert_awaited_once_with("u1", "Using: calculator")
        channel.on_stream_end.assert_awaited_once_with("u1", "hi world")



