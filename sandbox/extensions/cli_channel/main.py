"""CLI channel extension: reads stdin, sends user messages via Event Bus, prints responses."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)


class CliChannelExtension:
    """Extension + ChannelProvider: REPL loop; user input is emitted as user.message events."""

    def __init__(self) -> None:
        self.context: "ExtensionContext | None" = None
        self._input_task: asyncio.Task[Any] | None = None

    async def initialize(self, context: "ExtensionContext") -> None:
        self.context = context

    async def start(self) -> None:
        self._input_task = asyncio.create_task(self._input_loop(), name="cli_input_loop")

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

    async def _input_loop(self) -> None:
        assert self.context is not None, "initialize() must be called before start()"
        while True:
            try:
                line = await asyncio.to_thread(input, "> ")
            except (EOFError, KeyboardInterrupt):
                logger.info("CLI input stream closed")
                break
            line = line.strip()
            if not line:
                continue
            await self.context.emit(
                "user.message",
                {
                    "text": line,
                    "user_id": "cli_user",
                    "channel_id": self.context.extension_id,
                },
            )

    async def send_to_user(self, _user_id: str, message: str) -> None:
        print(message)
        print()

    async def send_message(self, message: str) -> None:
        """Proactive: deliver to CLI (stdout)."""
        print(message)
        print()
