"""Tests for telegram_channel setup without process restart."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.events.models import Event
from sandbox.extensions.telegram_channel.main import TelegramChannelExtension


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.extension_id = "telegram_channel"
    ctx.get_config.side_effect = lambda key, default=None: (
        "telegram_bot_token" if key == "token_secret" else default
    )
    ctx.logger = MagicMock()
    return ctx


@pytest.mark.asyncio
async def test_initialize_invalid_saved_token_does_not_raise() -> None:
    ext = TelegramChannelExtension()
    ctx = _make_context()
    kv = MagicMock()
    kv.get = AsyncMock(return_value=None)
    kv.set = AsyncMock()
    ctx.get_extension.return_value = kv
    ctx.get_secret = AsyncMock(return_value="invalid-token")

    await ext.initialize(ctx)

    assert ext._token is None
    assert ext._bot is None
    assert ext._dp is None


@pytest.mark.asyncio
async def test_apply_config_token_activates_runtime_without_restart() -> None:
    ext = TelegramChannelExtension()
    ctx = _make_context()
    kv = MagicMock()
    kv.get = AsyncMock(return_value=None)
    kv.set = AsyncMock()
    ctx.get_extension.return_value = kv
    ctx.get_secret = AsyncMock(return_value=None)
    ctx.set_secret = AsyncMock()

    await ext.initialize(ctx)
    await ext.apply_config("token", "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ")

    assert ext._token == "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
    assert ext._bot is not None
    assert ext._dp is not None
    ctx.set_secret.assert_called_once()


@pytest.mark.asyncio
async def test_initialize_subscribes_to_companion_presence_events() -> None:
    ext = TelegramChannelExtension()
    ctx = _make_context()
    kv = MagicMock()
    kv.get = AsyncMock(return_value=None)
    kv.set = AsyncMock()
    ctx.get_extension.return_value = kv
    ctx.get_secret = AsyncMock(return_value=None)
    ctx.subscribe_event = MagicMock()

    await ext.initialize(ctx)

    ctx.subscribe_event.assert_any_call(
        "companion.presence.updated",
        ext._on_companion_presence_updated,
    )


@pytest.mark.asyncio
async def test_presence_update_sends_rare_hint_for_significant_state() -> None:
    ext = TelegramChannelExtension()
    ext._presence_enabled = True
    ext._presence_cooldown_seconds = 3600
    ext._bot = MagicMock()
    ext._bot.send_message = AsyncMock()
    ext._token = "token"
    ext._chat_id = "12345"
    ext._ctx = _make_context()

    await ext._on_companion_presence_updated(
        Event(
            id=1,
            topic="companion.presence.updated",
            source="soul",
            payload={"presence_state": "REFLECTIVE", "phase": "REFLECTIVE"},
            created_at=0.0,
        )
    )

    ext._bot.send_message.assert_awaited_once()
    sent_text = ext._bot.send_message.await_args.kwargs["text"]
    assert "reflective" in sent_text.lower()


@pytest.mark.asyncio
async def test_presence_update_ignores_non_significant_state() -> None:
    ext = TelegramChannelExtension()
    ext._presence_enabled = True
    ext._bot = MagicMock()
    ext._bot.send_message = AsyncMock()
    ext._token = "token"
    ext._chat_id = "12345"
    ext._ctx = _make_context()

    await ext._on_companion_presence_updated(
        Event(
            id=1,
            topic="companion.presence.updated",
            source="soul",
            payload={"presence_state": "PLAYFUL", "phase": "CURIOUS"},
            created_at=0.0,
        )
    )

    ext._bot.send_message.assert_not_awaited()
