"""CLI channel extension: reads stdin, sends user messages via Event Bus, prints responses."""

import asyncio
import getpass
import logging
import sys
from typing import TYPE_CHECKING, Any

from core.events.topics import SystemTopics

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)


class CliChannelExtension:
    """Extension + ChannelProvider: REPL loop; user input is emitted as user.message events."""

    _RESPONSE_TIMEOUT_SEC = 120
    _INTERCEPT_GRACE_SEC = 0.25

    def __init__(self) -> None:
        self.context: ExtensionContext | None = None
        self._input_task: asyncio.Task[Any] | None = None
        self._streaming_enabled = True
        self._presence_enabled = True
        self._stream_buffer = ""
        self._intercept_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._intercept_pending = asyncio.Event()
        self._response_complete = asyncio.Event()
        self._response_complete.set()
        self._last_presence_key: str | None = None
        self._awaiting_input = False

    async def initialize(self, context: "ExtensionContext") -> None:
        self.context = context
        self._streaming_enabled = bool(context.get_config("streaming_enabled", True))
        self._presence_enabled = bool(context.get_config("presence_enabled", True))
        context.subscribe_event(
            SystemTopics.SECURE_INPUT_REQUEST,
            self._on_secure_input_request,
        )
        context.subscribe_event(
            SystemTopics.MCP_TOOL_APPROVAL_REQUEST,
            self._on_mcp_approval_request,
        )
        context.subscribe_event(
            "companion.presence.updated",
            self._on_companion_presence_updated,
        )
        context.subscribe_event(
            "companion.lifecycle.changed",
            self._on_companion_lifecycle_changed,
        )

    async def _on_secure_input_request(self, event: Any) -> None:
        """Enqueue secure input requests targeting this channel."""
        payload = event.payload
        target = payload.get("target_channel", "")
        if target == self.context.extension_id:
            self._intercept_queue.put_nowait({**payload, "_type": "secure_input"})
            self._intercept_pending.set()

    async def _on_mcp_approval_request(self, event: Any) -> None:
        """Enqueue MCP tool approval requests targeting this channel."""
        payload = event.payload
        target = payload.get("channel_id", "")
        if target is None or target == self.context.extension_id:
            self._intercept_queue.put_nowait({**payload, "_type": "mcp_approval"})
            self._intercept_pending.set()

    def _render_presence_line(self, payload: dict[str, Any]) -> str | None:
        presence = str(payload.get("presence_state") or "").strip().lower()
        phase = str(payload.get("phase") or "").strip().lower()
        if not presence:
            return None
        if phase:
            return f"[companion: {presence} · {phase}]"
        return f"[companion: {presence}]"

    async def _safe_print(self, text: str) -> None:
        """Print text immediately, even if the user is mid-input.

        When input() is blocking, uses ANSI escape to clear the current line,
        print the message, and re-display the prompt. The user's typed characters
        remain in the OS terminal buffer and will be submitted on Enter.
        """
        if self._awaiting_input:
            sys.stdout.write("\r\033[K")
            sys.stdout.write(text)
            sys.stdout.write("\n\n> ")
            sys.stdout.flush()
        else:
            print(text)
            print()

    async def _on_companion_presence_updated(self, event: Any) -> None:
        """Print a compact status line when visible presence changes."""
        if not self._presence_enabled:
            return
        payload = dict(event.payload or {})
        line = self._render_presence_line(payload)
        if not line:
            return
        key = line.lower()
        if key == self._last_presence_key:
            return
        self._last_presence_key = key
        await self._safe_print(line)

    async def _on_companion_lifecycle_changed(self, event: Any) -> None:
        """Print rare lifecycle milestones as a short status line."""
        if not self._presence_enabled:
            return
        payload = dict(event.payload or {})
        lifecycle = str(payload.get("new_lifecycle_phase") or "").strip().lower()
        if not lifecycle:
            return
        key = f"lifecycle:{lifecycle}"
        if key == self._last_presence_key:
            return
        self._last_presence_key = key
        await self._safe_print(f"[companion: {lifecycle}]")

    async def on_stream_start(self, _user_id: str) -> None:
        self._stream_buffer = ""

    async def on_stream_chunk(self, _user_id: str, chunk: str) -> None:
        if self._streaming_enabled:
            print(chunk, end="", flush=True)
            return
        self._stream_buffer += chunk

    async def on_stream_status(self, _user_id: str, status: str) -> None:
        if self._streaming_enabled:
            print(f"\n  [{status}]", flush=True)

    async def on_stream_end(self, user_id: str, full_text: str) -> None:
        if not self._streaming_enabled:
            await self.send_to_user(user_id, self._stream_buffer or full_text)
        else:
            print()
            print()
        self._response_complete.set()

    async def start(self) -> None:
        self._input_task = asyncio.create_task(
            self._input_loop(), name="cli_input_loop"
        )

    async def stop(self) -> None:
        if self._input_task:
            self._input_task.cancel()
            try:
                await self._input_task
            except asyncio.CancelledError:
                pass
            self._input_task = None

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        if self._input_task is None:
            return True  # not yet started or cleanly stopped
        return not self._input_task.done()

    async def _emit_user_message(self, text: str) -> None:
        """Emit user.message and mark a response as pending."""
        assert self.context is not None
        self._response_complete.clear()
        await self.context.emit(
            "user.message",
            {
                "text": text,
                "user_id": "cli_user",
                "channel_id": self.context.extension_id,
            },
        )

    async def _process_one_intercept(self) -> bool:
        """Process one pending intercept (MCP approval or secure input). Return True if processed."""
        try:
            req = self._intercept_queue.get_nowait()
        except asyncio.QueueEmpty:
            self._intercept_pending.clear()
            return False
        if req.get("_type") == "mcp_approval":
            await self._handle_mcp_approval(req)
        else:
            await self._handle_secure_input(req)
        if self._intercept_queue.empty():
            self._intercept_pending.clear()
        return True

    async def _wait_for_pending_intercept(self) -> bool:
        """Give freshly-published intercept events a brief chance to preempt plain input()."""
        if not self._intercept_queue.empty():
            return True
        try:
            await asyncio.wait_for(
                self._intercept_pending.wait(),
                timeout=self._INTERCEPT_GRACE_SEC,
            )
        except TimeoutError:
            return not self._intercept_queue.empty()
        return True

    async def _input_loop(self) -> None:
        assert self.context is not None, "initialize() must be called before start()"
        while True:
            if await self._process_one_intercept():
                continue
            if not self._response_complete.is_set():
                await asyncio.sleep(0.05)
                continue
            if await self._wait_for_pending_intercept():
                continue
            try:
                self._awaiting_input = True
                line = await asyncio.to_thread(input, "> ")
            except (EOFError, KeyboardInterrupt):
                logger.info("CLI input stream closed")
                break
            finally:
                self._awaiting_input = False
            line = line.strip()
            if not line:
                continue
            await self._emit_user_message(line)

    async def _handle_mcp_approval(self, req: dict[str, Any]) -> None:
        """Show MCP tool approval prompt, emit MCP_TOOL_APPROVAL_RESPONSE."""
        request_id = req.get("request_id", "")
        tool_name = req.get("tool_name", "?")
        arguments = req.get("arguments", "")
        if not request_id:
            return
        print(
            f"\n[MCP Approval] Tool '{tool_name}' requested with args: {arguments[:200]}"
            f"{'...' if len(arguments) > 200 else ''}\n"
            "Approve? [y/N]: ",
            end="",
            flush=True,
        )
        try:
            line = (await asyncio.to_thread(input)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            line = "n"
        approved = line in ("y", "yes")
        await self.context.emit(
            SystemTopics.MCP_TOOL_APPROVAL_RESPONSE,
            {
                "request_id": request_id,
                "approved": approved,
                "reason": None,
            },
        )
        print("Approved." if approved else "Rejected.")

    async def _read_secret_prompt(self, prompt: str) -> str | None:
        """Read a secret from the user. Returns None on cancel/EOF/empty.

        Uses getpass on every platform so the value is not echoed in terminal.
        """
        while True:
            try:
                value = await asyncio.to_thread(getpass.getpass, prompt)
            except (EOFError, KeyboardInterrupt):
                return None
            value = value.strip()
            if not value:
                continue
            if value.lower() == "cancel":
                return None
            return value

    async def _handle_secure_input(self, req: dict[str, Any]) -> None:
        """Collect secret via hidden prompt, store in keyring, emit synthetic confirmation."""
        secret_id = req["secret_id"]
        prompt = req["prompt"]
        framed = (
            f"\n[Security] The AI agent requests secure input: {prompt}\n"
            "(input hidden, type 'cancel' to abort): "
        )
        value = await self._read_secret_prompt(framed)
        if value is None:
            await self._emit_user_message(
                f"[System] Secret input for '{secret_id}' cancelled by user."
            )
            return
        try:
            await self.context.set_secret(secret_id, value)
        except Exception:
            logger.exception("Failed to store secret %s", secret_id)
            await self._emit_user_message(
                f"[System] Failed to save secret '{secret_id}'. Check keyring availability."
            )
            return
        await self._emit_user_message(
            f"[System] Secret '{secret_id}' saved successfully."
        )

    async def send_to_user(self, _user_id: str, message: str) -> None:
        print(message)
        print()

    async def send_message(self, message: str) -> None:
        """Proactive: deliver to CLI (stdout).

        If the user is currently typing (input() is blocking), the message
        is queued and displayed before the next prompt to avoid corrupting
        the input line.
        """
        await self._safe_print(message)
