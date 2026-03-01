"""Telegram channel extension: aiogram-based polling, Extension + ChannelProvider + ServiceProvider + SetupProvider + ContextProvider."""

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from formatting import escape_html, md_to_tg_html

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.utils.token import TokenValidationError, validate_token

if TYPE_CHECKING:
    from core.extensions.contract import TurnContext
    from core.extensions.context import ExtensionContext


# Typing action is shown for 5 seconds; repeat every 4 seconds while agent is working.
TYPING_HEARTBEAT_INTERVAL_SEC = 4


MAX_TG_MESSAGE_LEN = 4096


@dataclass
class StreamState:
    """Active stream state for a Telegram user."""

    message_id: int
    buffer: str = ""
    last_edit_at: float = 0.0  # 0.0 means "never edited" — first edit fires immediately
    typing_task: asyncio.Task[None] | None = None


class TelegramChannelExtension:
    """Extension + ChannelProvider + ServiceProvider + SetupProvider + ContextProvider.

    Single-user app: always sends to self._chat_id regardless of user_id argument.
    """

    def __init__(self) -> None:
        self._ctx: "ExtensionContext | None" = None
        self._kv: Any = None
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None
        self._token: str | None = None
        self._chat_id: str | None = None
        self._polling_timeout: int = 30
        self._streaming_enabled = True
        self._stream_edit_interval_ms = 500
        self._stream_min_chunk_chars = 20
        self._streams: dict[str, StreamState] = {}

    # ------------------------------------------------------------------ #
    # ContextProvider                                                       #
    # ------------------------------------------------------------------ #

    @property
    def context_priority(self) -> int:
        return 10  # inject before Memory (priority=100)

    async def get_context(
        self, prompt: str, turn_context: "TurnContext"
    ) -> str | None:
        if self._bot and self._token and self._chat_id:
            return (
                "## Available channels\n"
                "- telegram_channel — READY. "
                "Use send_to_channel(channel_id='telegram_channel', text=...) to send messages. "
                "Do NOT ask the user for chat_id or any Telegram credentials — everything is already configured.\n"
            )
        if self._token:
            return (
                "## Available channels\n"
                "- telegram_channel — TOKEN OK, awaiting first /start from user to capture chat_id. "
                "Cannot send proactive messages yet.\n"
            )
        return (
            "## Available channels\n"
            "- telegram_channel — NOT CONFIGURED (token missing). "
            "Ask the user to run Telegram setup before attempting to send.\n"
        )

    # ------------------------------------------------------------------ #
    # SetupProvider                                                         #
    # ------------------------------------------------------------------ #

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
                "description": "Telegram chat ID (auto-captured from first message if omitted)",
                "secret": False,
                "required": False,
            },
        ]

    async def apply_config(self, name: str, value: str) -> None:
        """SetupProvider: save token to keyring, chat_id to KV."""
        if not self._ctx:
            raise RuntimeError("Extension not initialized")
        value = (value or "").strip() or None
        if name == "token":
            token_key = self._ctx.get_config(
                "token_secret", f"{self._ctx.extension_id}_token"
            )
            if value:
                await self._ctx.set_secret(token_key, value)
            return
        kv = self._ctx.get_extension("kv")
        if not kv:
            raise RuntimeError("KV extension not available")
        key = f"{self._ctx.extension_id}.{name}"
        await kv.set(key, value)

    async def on_setup_complete(self) -> tuple[bool, str]:
        """SetupProvider: verify token is valid via Telegram API. chat_id is optional (auto-captured)."""
        if not self._ctx:
            return False, "Extension not initialized"
        token_key = self._ctx.get_config(
            "token_secret", f"{self._ctx.extension_id}_token"
        )
        token = await self._ctx.get_secret(token_key)
        if not token and self._kv:
            token = await self._kv.get(f"{self._ctx.extension_id}.token")
        token = (token or "").strip()
        if not token:
            return False, "token is required"
        try:
            validate_token(token)
        except TokenValidationError as e:
            return False, f"Invalid token format: {e}"
        try:
            bot = Bot(token=token)
            try:
                me = await bot.get_me()
                display = f"@{me.username}" if me.username else me.first_name
                return True, f"Telegram connected: {display}"
            finally:
                await bot.session.close()
        except Exception:
            return False, "Telegram API rejected the token"

    # ------------------------------------------------------------------ #
    # Lifecycle                                                             #
    # ------------------------------------------------------------------ #

    async def initialize(self, context: "ExtensionContext") -> None:
        self._ctx = context
        self._kv = context.get_extension("kv")
        if not self._kv:
            raise RuntimeError(
                "Telegram channel requires the KV extension. Add it to depends_on and ensure it is enabled."
            )

        self._streaming_enabled = bool(context.get_config("streaming_enabled", True))
        self._stream_edit_interval_ms = int(
            context.get_config("stream_edit_interval_ms", 500)
        )
        self._stream_min_chunk_chars = int(
            context.get_config("stream_min_chunk_chars", 20)
        )
        self._polling_timeout = int(context.get_config("polling_timeout", 30))

        token_key = context.get_config("token_secret", f"{context.extension_id}_token")
        token = await context.get_secret(token_key)
        if not token:
            token = await self._kv.get(f"{context.extension_id}.token")
        if token:
            token = token.strip()

        if not token:
            context.logger.info(
                "Telegram channel: no token. Use request_secure_input for secret '%s', "
                "then request_restart().",
                token_key,
            )
            self._token = None
            self._chat_id = None
            self._bot = None
            self._dp = None
            return

        try:
            validate_token(token)
        except TokenValidationError as e:
            raise RuntimeError(f"Invalid Telegram bot token: {e}") from e

        chat_id_raw = await self._kv.get(f"{context.extension_id}.chat_id")
        chat_id = str(chat_id_raw).strip() if chat_id_raw else None

        self._token = token
        self._chat_id = chat_id

        self._bot = Bot(
            token=self._token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self._dp = Dispatcher()

        ctx = context
        ext_id = context.extension_id
        kv = self._kv

        async def on_message(message: Message) -> None:
            if not message.text:
                return
            chat = message.chat
            if not chat:
                return
            msg_chat_id = str(chat.id)

            if self._chat_id is None:
                self._chat_id = msg_chat_id
                await kv.set(f"{ext_id}.chat_id", msg_chat_id)
                ctx.logger.info("Telegram channel: auto-saved chat_id=%s", msg_chat_id)

            if msg_chat_id != self._chat_id:
                return
            await ctx.emit(
                "user.message",
                {"text": message.text, "user_id": msg_chat_id, "channel_id": ext_id},
            )

        self._dp.message.register(on_message)
        if chat_id:
            context.logger.info(
                "Telegram channel initialized (polling, chat_id=%s)", chat_id
            )
        else:
            context.logger.info(
                "Telegram channel initialized (polling, waiting for first message to capture chat_id)"
            )

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
        return bool(self._bot and self._token and self._chat_id)

    # ------------------------------------------------------------------ #
    # ServiceProvider                                                       #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    # Streaming helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _typing_heartbeat(self) -> None:
        """Send typing action every 4 seconds (Telegram shows it for 5s). Run as task until cancelled."""
        try:
            while True:
                await asyncio.sleep(TYPING_HEARTBEAT_INTERVAL_SEC)
                if not self._bot or not self._chat_id:
                    break
                try:
                    await self._bot.send_chat_action(
                        chat_id=self._chat_id, action="typing"
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    async def on_stream_start(self, user_id: str) -> None:
        if not self._streaming_enabled or not self._bot:
            return
        try:
            await self._bot.send_chat_action(chat_id=self._chat_id, action="typing")
            message = await self._bot.send_message(
                chat_id=self._chat_id,
                text="...",
                parse_mode=ParseMode.HTML,
            )
            state = StreamState(message_id=message.message_id, last_edit_at=0.0)
            state.typing_task = asyncio.create_task(self._typing_heartbeat())
            self._streams[user_id] = state
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
                text=escape_html(state.buffer),
                parse_mode=ParseMode.HTML,
            )
            state.last_edit_at = now
        except Exception:
            pass

    async def on_stream_status(self, user_id: str, status: str) -> None:
        if not self._streaming_enabled or not self._bot:
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
        if state.typing_task is not None:
            state.typing_task.cancel()
            try:
                await state.typing_task
            except asyncio.CancelledError:
                pass
        try:
            formatted = md_to_tg_html(full_text)
            if len(formatted) <= MAX_TG_MESSAGE_LEN:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=state.message_id,
                    text=formatted,
                    parse_mode=ParseMode.HTML,
                )
            else:
                parts = [
                    formatted[i : i + MAX_TG_MESSAGE_LEN]
                    for i in range(0, len(formatted), MAX_TG_MESSAGE_LEN)
                ]
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=state.message_id,
                    text=parts[0],
                    parse_mode=ParseMode.HTML,
                )
                for part in parts[1:]:
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=part,
                        parse_mode=ParseMode.HTML,
                    )
        except Exception as e:
            if self._ctx:
                self._ctx.logger.exception(
                    "Failed to finalize stream for %s: %s", user_id, e
                )

    # ------------------------------------------------------------------ #
    # ChannelProvider                                                       #
    # ------------------------------------------------------------------ #

    async def send_to_user(self, user_id: str, message: str) -> None:
        """ChannelProvider: deliver message to the configured chat_id.

        Single-user app: user_id argument is ignored — always sends to self._chat_id.
        Raises RuntimeError if the channel is not ready so the agent gets an explicit
        error instead of a silent no-op.
        """
        if not self._bot or not self._token or not self._chat_id:
            raise RuntimeError(
                "telegram_channel is not ready: token or chat_id missing. "
                "Ask the user to complete Telegram setup."
            )
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=md_to_tg_html(message),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            if self._ctx:
                self._ctx.logger.exception("Failed to send message: %s", e)
            raise

    async def send_message(self, message: str) -> None:
        """Proactive: deliver to the channel's default recipient. Alias for send_to_user."""
        await self.send_to_user(self._chat_id or "", message)
