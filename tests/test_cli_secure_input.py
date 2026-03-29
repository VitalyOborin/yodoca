"""Tests for CLI channel secure input interceptor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.events.models import Event
from core.events.topics import SystemTopics
from sandbox.extensions.cli_channel.main import CliChannelExtension


def _make_ext() -> tuple[CliChannelExtension, MagicMock]:
    """Create a CliChannelExtension wired to a mock context."""
    ext = CliChannelExtension()
    ctx = MagicMock()
    ctx.extension_id = "cli_channel"
    ctx.emit = AsyncMock()
    ctx.set_secret = AsyncMock()
    ext.context = ctx
    return ext, ctx


class TestSecureInputRequestHandler:
    """_on_secure_input_request enqueues only when target_channel matches."""

    @pytest.mark.asyncio
    async def test_matching_channel_enqueues(self) -> None:
        ext, _ = _make_ext()
        event = Event(
            id=1,
            topic=SystemTopics.SECURE_INPUT_REQUEST,
            source="kernel",
            payload={
                "secret_id": "telegram_token",
                "prompt": "Enter token",
                "target_channel": "cli_channel",
            },
            created_at=0.0,
        )
        await ext._on_secure_input_request(event)
        assert not ext._intercept_queue.empty()
        assert ext._intercept_pending.is_set()
        req = ext._intercept_queue.get_nowait()
        assert req["secret_id"] == "telegram_token"
        assert req["prompt"] == "Enter token"
        assert req["target_channel"] == "cli_channel"

    @pytest.mark.asyncio
    async def test_non_matching_channel_ignores(self) -> None:
        ext, _ = _make_ext()
        event = Event(
            id=1,
            topic=SystemTopics.SECURE_INPUT_REQUEST,
            source="kernel",
            payload={
                "secret_id": "telegram_token",
                "prompt": "Enter token",
                "target_channel": "telegram_channel",
            },
            created_at=0.0,
        )
        await ext._on_secure_input_request(event)
        assert ext._intercept_queue.empty()


class TestHandleSecureInput:
    """_handle_secure_input: cancel, success, empty re-prompt."""

    @pytest.mark.asyncio
    async def test_cancel_emits_cancellation(self) -> None:
        ext, ctx = _make_ext()
        req = {"secret_id": "my_secret", "prompt": "Enter value"}

        with patch("asyncio.to_thread", new_callable=AsyncMock) as to_thread:
            to_thread.return_value = "cancel"
            await ext._handle_secure_input(req)

        ctx.emit.assert_called_once()
        call_payload = ctx.emit.call_args[0][1]
        assert (
            call_payload["text"]
            == "[System] Secret input for 'my_secret' cancelled by user."
        )
        ctx.set_secret.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_stores_and_emits_confirmation(self) -> None:
        ext, ctx = _make_ext()
        req = {"secret_id": "telegram_token", "prompt": "Enter token"}

        with patch("asyncio.to_thread", new_callable=AsyncMock) as to_thread:
            to_thread.return_value = "sk-secret-value"
            await ext._handle_secure_input(req)

        ctx.set_secret.assert_called_once_with("telegram_token", "sk-secret-value")
        ctx.emit.assert_called_once()
        call_payload = ctx.emit.call_args[0][1]
        assert "saved successfully" in call_payload["text"]
        assert "telegram_token" in call_payload["text"]

    @pytest.mark.asyncio
    async def test_empty_then_value_stores_on_second_input(self) -> None:
        ext, ctx = _make_ext()
        req = {"secret_id": "x", "prompt": "Enter"}

        with patch("asyncio.to_thread", new_callable=AsyncMock) as to_thread:
            to_thread.side_effect = ["", "secret123"]
            await ext._handle_secure_input(req)

        assert to_thread.call_count == 2
        ctx.set_secret.assert_called_once_with("x", "secret123")


class TestResponseComplete:
    """_response_complete gate prevents input() from racing the EventBus."""

    @pytest.mark.asyncio
    async def test_emit_user_message_clears_response_complete(self) -> None:
        ext, _ = _make_ext()
        assert ext._response_complete.is_set()
        await ext._emit_user_message("hello")
        assert not ext._response_complete.is_set()

    @pytest.mark.asyncio
    async def test_on_stream_end_sets_response_complete(self) -> None:
        ext, _ = _make_ext()
        ext._response_complete.clear()
        await ext.on_stream_end("cli_user", "done")
        assert ext._response_complete.is_set()

    @pytest.mark.asyncio
    async def test_handle_secure_input_clears_response_complete(self) -> None:
        """After saving a secret the confirmation emit marks response pending."""
        ext, _ = _make_ext()
        req = {"secret_id": "tok", "prompt": "Enter"}
        with patch("asyncio.to_thread", new_callable=AsyncMock) as to_thread:
            to_thread.return_value = "secret-val"
            await ext._handle_secure_input(req)
        assert not ext._response_complete.is_set()


class TestInterceptGraceWindow:
    """Input loop waits briefly so intercepts can preempt plain input()."""

    @pytest.mark.asyncio
    async def test_wait_for_pending_intercept_returns_true_when_event_arrives(
        self,
    ) -> None:
        ext, _ = _make_ext()

        async def publish_intercept() -> None:
            await asyncio.sleep(0)
            ext._intercept_queue.put_nowait({"_type": "secure_input"})
            ext._intercept_pending.set()

        task = asyncio.create_task(publish_intercept())
        assert await ext._wait_for_pending_intercept() is True
        await task

    @pytest.mark.asyncio
    async def test_process_one_intercept_clears_pending_flag_when_queue_empty(
        self,
    ) -> None:
        ext, _ = _make_ext()
        ext._intercept_queue.put_nowait(
            {"_type": "secure_input", "secret_id": "x", "prompt": "p"}
        )
        ext._intercept_pending.set()

        ext._handle_secure_input = AsyncMock()
        assert await ext._process_one_intercept() is True
        assert not ext._intercept_pending.is_set()


class TestCompanionPresence:
    """CLI prints lightweight presence lines for companion events."""

    @pytest.mark.asyncio
    async def test_presence_update_prints_deduped_status_line(self) -> None:
        ext, _ = _make_ext()

        with patch("builtins.print") as print_mock:
            await ext._on_companion_presence_updated(
                Event(
                    id=1,
                    topic="companion.presence.updated",
                    source="soul",
                    payload={"presence_state": "WARM", "phase": "SOCIAL"},
                    created_at=0.0,
                )
            )
            await ext._on_companion_presence_updated(
                Event(
                    id=2,
                    topic="companion.presence.updated",
                    source="soul",
                    payload={"presence_state": "WARM", "phase": "SOCIAL"},
                    created_at=1.0,
                )
            )

        assert print_mock.call_count == 2
        assert any(
            "[companion: warm · social]" in str(call.args[0])
            for call in print_mock.call_args_list
            if call.args
        )

    @pytest.mark.asyncio
    async def test_lifecycle_change_prints_status_line(self) -> None:
        ext, _ = _make_ext()

        with patch("builtins.print") as print_mock:
            await ext._on_companion_lifecycle_changed(
                Event(
                    id=1,
                    topic="companion.lifecycle.changed",
                    source="soul",
                    payload={"new_lifecycle_phase": "FORMING"},
                    created_at=0.0,
                )
            )

        assert any(
            "[companion: forming]" in str(call.args[0])
            for call in print_mock.call_args_list
            if call.args
        )
