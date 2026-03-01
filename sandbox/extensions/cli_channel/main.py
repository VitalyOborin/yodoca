"""CLI channel extension: reads stdin, sends user messages via Event Bus, prints responses."""

import asyncio
import getpass
import logging
from typing import TYPE_CHECKING, Any

from core.events.topics import SystemTopics

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)


class CliChannelExtension:
    """Extension + ChannelProvider: REPL loop; user input is emitted as user.message events."""

    _RESPONSE_TIMEOUT_SEC = 120

    def __init__(self) -> None:
        self.context: "ExtensionContext | None" = None
        self._input_task: asyncio.Task[Any] | None = None
        self._streaming_enabled = True
        self._stream_buffer = ""
        self._intercept_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._response_complete = asyncio.Event()
        self._response_complete.set()

    async def initialize(self, context: "ExtensionContext") -> None:
        self.context = context
        self._streaming_enabled = bool(context.get_config("streaming_enabled", True))
        context.subscribe_event(
            SystemTopics.SECURE_INPUT_REQUEST,
            self._on_secure_input_request,
        )
        context.subscribe_event(
            SystemTopics.MCP_TOOL_APPROVAL_REQUEST,
            self._on_mcp_approval_request,
        )

    async def _on_secure_input_request(self, event: Any) -> None:
        """Enqueue secure input requests targeting this channel."""
        payload = event.payload
        target = payload.get("target_channel", "")
        if target == self.context.extension_id:
            self._intercept_queue.put_nowait({**payload, "_type": "secure_input"})

    async def _on_mcp_approval_request(self, event: Any) -> None:
        """Enqueue MCP tool approval requests targeting this channel."""
        payload = event.payload
        target = payload.get("channel_id", "")
        if target is None or target == self.context.extension_id:
            self._intercept_queue.put_nowait({**payload, "_type": "mcp_approval"})

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

    async def _input_loop(self) -> None:
        assert self.context is not None, "initialize() must be called before start()"
        while True:
            # Block until the agent finishes its current response so that
            # any follow-up events (e.g. SECURE_INPUT_REQUEST) published
            # during that response have time to reach our intercept queue
            # before we show the next ``>`` prompt.
            if not self._response_complete.is_set():
                try:
                    await asyncio.wait_for(
                        self._response_complete.wait(),
                        timeout=self._RESPONSE_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Timed out waiting for agent response")
                    self._response_complete.set()
                # The EventBus dispatch loop delivers SECURE_INPUT_REQUEST
                # asynchronously; yield briefly so it completes delivery.
                await asyncio.sleep(0.05)

            if not self._intercept_queue.empty():
                try:
                    req = self._intercept_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                else:
                    if req.get("_type") == "mcp_approval":
                        await self._handle_mcp_approval(req)
                    else:
                        await self._handle_secure_input(req)
                    continue

            try:
                line = await asyncio.to_thread(input, "> ")
            except (EOFError, KeyboardInterrupt):
                logger.info("CLI input stream closed")
                break
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

    async def _handle_secure_input(self, req: dict[str, Any]) -> None:
        """Collect secret via getpass, store in keyring, emit synthetic confirmation."""
        secret_id = req["secret_id"]
        prompt = req["prompt"]
        framed = (
            f"\n[Security] The AI agent requests secure input: {prompt}\n"
            "(input hidden, type 'cancel' to abort): "
        )
        while True:
            try:
                value = await asyncio.to_thread(getpass.getpass, framed)
            except (EOFError, KeyboardInterrupt):
                await self._emit_user_message(
                    f"[System] Secret input for '{secret_id}' cancelled by user."
                )
                return
            value = value.strip()
            if not value:
                continue
            if value.lower() == "cancel":
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
            return

    async def send_to_user(self, _user_id: str, message: str) -> None:
        print(message)
        print()

    async def send_message(self, message: str) -> None:
        """Proactive: deliver to CLI (stdout)."""
        print(message)
        print()
