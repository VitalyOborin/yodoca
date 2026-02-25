"""Tests for CLI channel secure input interceptor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.events.models import Event
from core.events.topics import SystemTopics
from sandbox.extensions.cli_channel.main import CliChannelExtension


class TestSecureInputRequestHandler:
    """_on_secure_input_request enqueues only when target_channel matches."""

    @pytest.mark.asyncio
    async def test_matching_channel_enqueues(self) -> None:
        ext = CliChannelExtension()
        ctx = MagicMock()
        ctx.extension_id = "cli_channel"
        ext.context = ctx
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
        req = ext._intercept_queue.get_nowait()
        assert req["secret_id"] == "telegram_token"
        assert req["prompt"] == "Enter token"
        assert req["target_channel"] == "cli_channel"

    @pytest.mark.asyncio
    async def test_non_matching_channel_ignores(self) -> None:
        ext = CliChannelExtension()
        ctx = MagicMock()
        ctx.extension_id = "cli_channel"
        ext.context = ctx
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
        ext = CliChannelExtension()
        ctx = MagicMock()
        ctx.extension_id = "cli_channel"
        ctx.emit = AsyncMock()
        ctx.set_secret = AsyncMock()
        ext.context = ctx
        req = {"secret_id": "my_secret", "prompt": "Enter value"}

        with patch("asyncio.to_thread", new_callable=AsyncMock) as to_thread:
            to_thread.return_value = "cancel"
            await ext._handle_secure_input(req)

        ctx.emit.assert_called_once()
        call_payload = ctx.emit.call_args[0][1]
        assert call_payload["text"] == "[System] Secret input for 'my_secret' cancelled by user."
        ctx.set_secret.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_stores_and_emits_confirmation(self) -> None:
        ext = CliChannelExtension()
        ctx = MagicMock()
        ctx.extension_id = "cli_channel"
        ctx.emit = AsyncMock()
        ctx.set_secret = AsyncMock()
        ext.context = ctx
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
        ext = CliChannelExtension()
        ctx = MagicMock()
        ctx.extension_id = "cli_channel"
        ctx.emit = AsyncMock()
        ctx.set_secret = AsyncMock()
        ext.context = ctx
        req = {"secret_id": "x", "prompt": "Enter"}

        with patch("asyncio.to_thread", new_callable=AsyncMock) as to_thread:
            to_thread.side_effect = ["", "secret123"]
            await ext._handle_secure_input(req)

        assert to_thread.call_count == 2
        ctx.set_secret.assert_called_once_with("x", "secret123")
