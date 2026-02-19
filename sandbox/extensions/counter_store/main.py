"""Counter store extension: ServiceProvider + ToolProvider. In-memory counter with tools and background state dump to log."""

import asyncio
from typing import Any

from agents import function_tool


class CounterStoreExtension:
    """Extension + ToolProvider + ServiceProvider: counter with increment/get_counter tools and background logging."""

    def __init__(self) -> None:
        self._counter = 0
        self._logger: Any = None

    async def initialize(self, context: Any) -> None:
        self._logger = context.logger

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return True

    def get_tools(self) -> list[Any]:
        ext = self

        @function_tool
        def increment(amount: int = 1) -> str:
            """Increment counter by amount. Returns current value."""
            ext._counter += amount
            return f"counter = {ext._counter}"

        @function_tool
        def get_counter() -> str:
            """Get current counter value."""
            return f"counter = {ext._counter}"

        return [increment, get_counter]

    async def run_background(self) -> None:
        while True:
            try:
                await asyncio.sleep(30)
                self._logger.info("counter state: %d", self._counter)
            except asyncio.CancelledError:
                break
