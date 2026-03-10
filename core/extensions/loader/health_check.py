"""HealthCheckManager: periodic health checks for extensions."""

import asyncio
import logging
from typing import TYPE_CHECKING

from core.extensions.contract import ExtensionState
from core.extensions.loader.lifecycle import ExtensionStateMachine, TaskSupervisor

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
        self._state_machine = ExtensionStateMachine(state)
        self._tasks = TaskSupervisor()

    def start(self) -> None:
        """Start the health check loop as an asyncio task."""
        self._tasks.start("health-check", self._loop)

    async def stop(self) -> None:
        """Cancel and await the health check task."""
        await self._tasks.stop("health-check")

    async def _loop(self) -> None:
        """Every 30s call health_check(); on False set ERROR and stop()."""
        while True:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
            for ext_id, ext in list(self._extensions.items()):
                if self._state.get(ext_id) != ExtensionState.ACTIVE:
                    continue
                try:
                    if not ext.health_check():
                        self._state_machine.mark_error(ext_id)
                        await ext.stop()
                except Exception as e:
                    logger.exception("health_check failed for %s: %s", ext_id, e)
                    self._state_machine.mark_error(ext_id)
                    await ext.stop()
