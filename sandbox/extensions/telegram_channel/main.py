"""Telegram channel extension: aiogram-based polling, Extension + ChannelProvider + ServiceProvider."""

import asyncio
from typing import TYPE_CHECKING, Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.utils.token import TokenValidationError, validate_token

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext

class TelegramChannelExtension:
    """Extension + ChannelProvider + ServiceProvider: Telegram Bot API via aiogram long-polling.

    Receives user messages via polling, emits user.message events.
    Sends agent responses via send_to_user.
    """

    def __init__(self) -> None:
        self._ctx: "ExtensionContext | None" = None
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None
        self._token: str | None = None
        self._chat_id: str | None = None
        self._polling_timeout: int = 30

    async def initialize(self, context: "ExtensionContext") -> None:
        self._ctx = context
        kv = context.get_extension("kv")
        if not kv:
            raise RuntimeError(
                "Telegram channel requires the KV extension. Add it to depends_on and ensure it is enabled."
            )

        token = await kv.get("telegram_channel.token")
        if token:
            token = token.strip()
        if not token:
            raise RuntimeError(
                "Telegram bot token missing. Set KV key 'telegram_channel.token' and restart."
            )

        try:
            validate_token(token)
        except TokenValidationError as e:
            raise RuntimeError(f"Invalid Telegram bot token: {e}") from e

        self._token = token

        chat_id = await kv.get("telegram_channel.chat_id")
        if chat_id:
            chat_id = str(chat_id).strip()
        if not chat_id:
            raise RuntimeError(
                "Telegram chat_id missing. Set KV key 'telegram_channel.chat_id' and restart."
            )
        self._chat_id = chat_id

        self._polling_timeout = int(context.get_config("polling_timeout", 30))

        self._bot = Bot(
            token=self._token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self._dp = Dispatcher()

        ctx = context
        ext_id = context.extension_id

        async def on_message(message: Message) -> None:
            if not message.text:
                return
            chat = message.chat
            if not chat:
                return
            msg_chat_id = str(chat.id)
            if msg_chat_id != self._chat_id:
                return
            await ctx.emit(
                "user.message",
                {"text": message.text, "user_id": msg_chat_id, "channel_id": ext_id},
            )

        self._dp.message.register(on_message)
        context.logger.info("Telegram channel initialized (polling, chat_id=%s)", self._chat_id)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        """Loader cancels run_background task; no extra cleanup needed."""
        pass

    async def destroy(self) -> None:
        if self._bot:
            try:
                await self._bot.session.close()
            except Exception:
                pass
            self._bot = None
        self._dp = None
        self._token = None

    def health_check(self) -> bool:
        return bool(self._bot and self._token)

    async def run_background(self) -> None:
        """ServiceProvider: run aiogram long-polling until cancelled."""
        if not self._bot or not self._dp or not self._token:
            return
        try:
            await self._dp.start_polling(
                self._bot,
                handle_signals=False,
                polling_timeout=self._polling_timeout,
                allowed_updates=["message"],
            )
        except asyncio.CancelledError:
            raise
        finally:
            if self._bot:
                try:
                    await self._bot.session.close()
                except Exception:
                    pass

    async def send_to_user(self, user_id: str, message: str) -> None:
        """ChannelProvider: deliver agent response to user."""
        if not self._bot or not self._token or not self._chat_id:
            return
        if str(user_id) != self._chat_id:
            return
        try:
            await self._bot.send_message(chat_id=self._chat_id, text=message)
        except Exception as e:
            if self._ctx:
                self._ctx.logger.exception("Failed to send to %s: %s", self._chat_id, e)
