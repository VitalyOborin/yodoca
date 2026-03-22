"""Tests for thread rotation in MessageRouter."""

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.events.topics import SystemTopics
from core.extensions.contract import ChannelProvider
from core.extensions.routing.router import MessageRouter


class MockChannel(ChannelProvider):
    async def send_to_user(self, user_id: str, message: str) -> None:
        pass

    async def send_message(self, message: str) -> None:
        pass


class TestThreadRotation:
    """Thread rotation on inactivity timeout."""

    @pytest.mark.asyncio
    async def test_rotate_thread_publishes_thread_completed(
        self, tmp_path: Path
    ) -> None:
        router = MessageRouter()
        mock_event_bus = MagicMock()
        mock_event_bus.publish = AsyncMock(return_value=1)
        # Distinct values ensure configure_thread and rotate_thread use different IDs.
        with patch("core.extensions.persistence.thread_manager.time") as mock_time:
            mock_time.time.side_effect = [1000.0, 1001.0]
            router.configure_thread(
                thread_db_path=str(tmp_path / "thread.db"),
                thread_timeout=1800,
                event_bus=mock_event_bus,
            )
        assert router._thread_id is not None
        old_id = router._thread_id

        with patch("core.extensions.persistence.thread_manager.time") as mock_time:
            mock_time.time.return_value = 1002.0
            await router._rotate_thread()

        assert router._thread_id != old_id
        mock_event_bus.publish.assert_called_once()
        call_args = mock_event_bus.publish.call_args
        assert call_args[0][0] == SystemTopics.THREAD_COMPLETED
        assert call_args[0][1] == "kernel"
        assert call_args[0][2]["thread_id"] == old_id
        assert call_args[0][2]["reason"] == "inactivity_timeout"

    @pytest.mark.asyncio
    async def test_handle_user_message_rotates_on_inactivity(
        self, tmp_path: Path
    ) -> None:
        router = MessageRouter()
        mock_event_bus = MagicMock()
        mock_event_bus.publish = AsyncMock(return_value=1)
        router.configure_thread(
            thread_db_path=str(tmp_path / "thread.db"),
            thread_timeout=1,
            event_bus=mock_event_bus,
        )
        router.set_agent(MagicMock())
        ch = MockChannel()

        mock_result = MagicMock()
        mock_result.final_output = "ok"

        with patch("agents.Runner") as mock_runner_cls:
            mock_runner_cls.run = AsyncMock(return_value=mock_result)
            await router.handle_user_message("first", "u1", ch, "cli")

        assert mock_event_bus.publish.call_count == 0

        time.sleep(1.1)

        with patch("agents.Runner") as mock_runner_cls2:
            mock_runner_cls2.run = AsyncMock(return_value=mock_result)
            await router.handle_user_message("second", "u1", ch, "cli")

        assert mock_event_bus.publish.call_count == 1

    @pytest.mark.asyncio
    async def test_no_rotation_when_within_timeout(self, tmp_path: Path) -> None:
        router = MessageRouter()
        mock_event_bus = MagicMock()
        mock_event_bus.publish = AsyncMock(return_value=1)
        router.configure_thread(
            thread_db_path=str(tmp_path / "thread.db"),
            thread_timeout=60,
            event_bus=mock_event_bus,
        )
        router.set_agent(MagicMock())
        ch = MockChannel()

        mock_result = MagicMock()
        mock_result.final_output = "ok"

        with patch("agents.Runner") as mock_runner_cls:
            mock_runner_cls.run = AsyncMock(return_value=mock_result)
            await router.handle_user_message("first", "u1", ch, "cli")
            await router.handle_user_message("second", "u1", ch, "cli")

        assert mock_event_bus.publish.call_count == 0
