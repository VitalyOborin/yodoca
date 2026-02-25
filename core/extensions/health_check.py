"""HealthCheckManager: periodic health checks for extensions."""

import asyncio
import logging
from typing import TYPE_CHECKING

from core.extensions.contract import ExtensionState

if TYPE_CHECKING:
    from core.extensions.contract import Extension

logger = logging.getLogger(__name__)

_HEALTH_CHECK_INTERVAL = 30.0


class HealthCheckManager:
    """Periodic health check loop for extensions. Mutates shared state on failure."""

    def __init__(
        self,
        extensions: dict[str, "Extension"],
        state: dict[str, ExtensionState],
    ) -> None:
        self._extensions = extensions
        self._state = state
        self._task: asyncio.Task[object] | None = None

    def start(self) -> None:
        """Start the health check loop as an asyncio task."""
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel and await the health check task."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        """Every 30s call health_check(); on False set ERROR and stop()."""
        while True:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
            for ext_id, ext in list(self._extensions.items()):
                if self._state.get(ext_id) != ExtensionState.ACTIVE:
                    continue
                try:
                    if not ext.health_check():
                        self._state[ext_id] = ExtensionState.ERROR
                        await ext.stop()
                except Exception as e:
                    logger.exception("health_check failed for %s: %s", ext_id, e)
                    self._state[ext_id] = ExtensionState.ERROR
                    await ext.stop()
