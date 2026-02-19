"""Echo service extension: ServiceProvider that logs every 10 seconds. Verifies run_background runs as task and handles CancelledError."""

import asyncio
from typing import Any


class EchoServiceExtension:
    """Extension + ServiceProvider: background loop logging ticks."""

    def __init__(self) -> None:
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

    async def run_background(self) -> None:
        counter = 0
        while True:
            try:
                await asyncio.sleep(10)
                counter += 1
                msg = f"echo_service tick #{counter}"
                self._logger.info(msg)
                print(msg)  # ensure visible in console when no logging config
            except asyncio.CancelledError:
                break
