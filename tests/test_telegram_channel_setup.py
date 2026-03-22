"""Tests for telegram_channel setup without process restart."""

from unittest.mock import AsyncMock, MagicMock

import pytest

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
