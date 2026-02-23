"""Telegram channel extension: aiogram-based polling, Extension + ChannelProvider + ServiceProvider + SetupProvider."""

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.utils.token import TokenValidationError, validate_token

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext


@dataclass
class StreamState:
    """Active stream state for a Telegram user."""

    message_id: int
    buffer: str = ""
    last_edit_at: float = 0.0


class TelegramChannelExtension:
    """Extension + ChannelProvider + ServiceProvider + SetupProvider: Telegram Bot API via aiogram long-polling.

    Receives user messages via polling, emits user.message events.
    Sends agent responses via send_to_user.
    Supports interactive setup via SetupProvider.
    """

    def __init__(self) -> None:
        self._ctx: "ExtensionContext | None" = None
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None
        self._token: str | None = None
        self._chat_id: str | None = None
        self._polling_timeout: int = 30
        self._streaming_enabled = True
        self._stream_edit_interval_ms = 500
        self._stream_min_chunk_chars = 20
        self._streams: dict[str, StreamState] = {}

    def get_setup_schema(self) -> list[dict]:
        """SetupProvider: schema for interactive configuration."""
        return [
            {
                "name": "token",
                "description": "Telegram Bot API token from @BotFather",
                "secret": True,
                "required": True,
            },
            {
                "name": "chat_id",
                "description": "Telegram chat ID to receive messages (single-user mode)",
                "secret": False,
                "required": True,
            },
        ]

    async def apply_config(self, name: str, value: str) -> None:
        """SetupProvider: save config value to KV."""
        if not self._ctx:
            raise RuntimeError("Extension not initialized")
        kv = self._ctx.get_extension("kv")
        if not kv:
            raise RuntimeError("KV extension not available")
        key = f"telegram_channel.{name}"
        await kv.set(key, value.strip() if value else None)

    async def on_setup_complete(self) -> tuple[bool, str]:
        """SetupProvider: verify token and chat_id are set and valid."""
        if not self._ctx:
            return False, "Extension not initialized"
        kv = self._ctx.get_extension("kv")
        if not kv:
            return False, "KV extension not available"
        token = await kv.get("telegram_channel.token")
        chat_id = await kv.get("telegram_channel.chat_id")
        if not token or not token.strip():
            return False, "token is required"
        if not chat_id or not str(chat_id).strip():
            return False, "chat_id is required"
        try:
            validate_token(token.strip())
        except TokenValidationError as e:
            return False, f"Invalid token: {e}"
        return True, "Telegram channel configured successfully"

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
        chat_id_raw = await kv.get("telegram_channel.chat_id")
        chat_id = str(chat_id_raw).strip() if chat_id_raw else None

        if not token or not chat_id:
            context.logger.info(
                "Telegram channel not configured. Use SetupProvider (get_setup_schema, apply_config, on_setup_complete) or set KV keys 'telegram_channel.token' and 'telegram_channel.chat_id'."
            )
            self._streaming_enabled = bool(context.get_config("streaming_enabled", True))
            self._stream_edit_interval_ms = int(
                context.get_config("stream_edit_interval_ms", 500)
            )
            self._stream_min_chunk_chars = int(context.get_config("stream_min_chunk_chars", 20))
            self._polling_timeout = int(context.get_config("polling_timeout", 30))
            self._token = None
            self._chat_id = None
            self._bot = None
            self._dp = None
            return

        self._streaming_enabled = bool(context.get_config("streaming_enabled", True))
        self._stream_edit_interval_ms = int(context.get_config("stream_edit_interval_ms", 500))
        self._stream_min_chunk_chars = int(context.get_config("stream_min_chunk_chars", 20))
        self._polling_timeout = int(context.get_config("polling_timeout", 30))

        try:
            validate_token(token)
        except TokenValidationError as e:
            raise RuntimeError(f"Invalid Telegram bot token: {e}") from e

        self._token = token
        self._chat_id = chat_id

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

    async def on_stream_start(self, user_id: str) -> None:
        if not self._streaming_enabled or not self._bot:
            return
        try:
            message = await self._bot.send_message(chat_id=self._chat_id, text="...")
            self._streams[user_id] = StreamState(message_id=message.message_id, last_edit_at=0.0)
        except Exception as e:
            if self._ctx:
                self._ctx.logger.exception(
                    "Failed to start stream for %s: %s", user_id, e
                )

    async def on_stream_chunk(self, user_id: str, chunk: str) -> None:
        if not self._streaming_enabled or not self._bot:
            return
        state = self._streams.get(user_id)
        if not state:
            return
        state.buffer += chunk
        if len(state.buffer) < self._stream_min_chunk_chars:
            return
        now = time.monotonic() * 1000
        if now - state.last_edit_at < self._stream_edit_interval_ms:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=state.message_id,
                text=state.buffer,
            )
            state.last_edit_at = now
        except Exception:
            # Swallow transient Telegram errors; next chunk will try again.
            pass

    async def on_stream_status(self, user_id: str, status: str) -> None:
        if not self._streaming_enabled or not self._bot:
            return
        if str(user_id) != str(self._chat_id):
            return
        try:
            await self._bot.send_chat_action(chat_id=self._chat_id, action="typing")
        except Exception:
            return

    async def on_stream_end(self, user_id: str, full_text: str) -> None:
        if not self._bot:
            return
        if not self._streaming_enabled:
            await self.send_to_user(user_id, full_text)
            return
        state = self._streams.pop(user_id, None)
        if not state:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=state.message_id,
                text=full_text,
            )
        except Exception as e:
            if self._ctx:
                self._ctx.logger.exception(
                    "Failed to finalize stream for %s: %s", user_id, e
                )

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

    async def send_message(self, message: str) -> None:
        """Proactive: deliver to the channel's default recipient."""
        if not self._bot or not self._token or not self._chat_id:
            return
        try:
            await self._bot.send_message(chat_id=self._chat_id, text=message)
        except Exception as e:
            if self._ctx:
                self._ctx.logger.exception("Failed to send message: %s", e)
