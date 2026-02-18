"""CLI channel extension: reads stdin, sends user messages to agent, prints responses."""

import asyncio
from typing import Any

id = "cli_channel"
name = "CLI Channel"
version = "1.0.0"


class CliChannelExtension:
    """Extension + ChannelProvider: REPL loop via on_user_message callback."""

    id = "cli_channel"
    name = "CLI Channel"
    version = "1.0.0"

    def __init__(self) -> None:
        self.context: Any = None
        self._input_task: asyncio.Task[Any] | None = None

    async def initialize(self, context: Any) -> None:
        self.context = context

    async def start(self) -> None:
        self._input_task = asyncio.create_task(self._input_loop())

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
        return True

    async def _input_loop(self) -> None:
        while True:
            try:
                line = await asyncio.to_thread(input, "> ")
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if not line:
                continue
            await self.context.on_user_message(line, "cli_user", self)

    async def send_to_user(self, user_id: str, message: str) -> None:
        print(message)
        print()
