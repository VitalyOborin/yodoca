"""HealthCheckManager: periodic health checks for extensions."""

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from core.extensions.contract import ExtensionState
from core.extensions.loader.lifecycle import ExtensionStateMachine, TaskSupervisor

if TYPE_CHECKING:
    from core.extensions.contract import Extension

logger = logging.getLogger(__name__)

_HEALTH_CHECK_INTERVAL = 30.0
HealthFailureCallback = Callable[[str, str | None, str, str], Awaitable[None]]


class HealthCheckManager:
    """Periodic health check loop for extensions. Mutates shared state on failure."""

    def __init__(
        self,
        extensions: dict[str, "Extension"],
        state: dict[str, ExtensionState],
        on_failure: HealthFailureCallback | None = None,
    ) -> None:
        self._extensions = extensions
        self._state = state
        self._state_machine = ExtensionStateMachine(state)
        self._tasks = TaskSupervisor()
        self._on_failure = on_failure

    def start(self) -> None:
        self._tasks.start("health-check", self._loop)

    async def stop(self) -> None:
        await self._tasks.stop("health-check")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
            for ext_id, ext in list(self._extensions.items()):
                if self._state.get(ext_id) != ExtensionState.ACTIVE:
                    continue
                try:
                    if not ext.health_check():
                        self._state_machine.mark_error(ext_id)
                        if self._on_failure is not None:
                            await self._on_failure(
                                ext_id,
                                None,
                                "health_check returned False",
                                "",
                            )
                        await ext.stop()
                except Exception as e:
                    logger.exception("health_check failed for %s: %s", ext_id, e)
                    self._state_machine.mark_error(ext_id)
                    if self._on_failure is not None:
                        await self._on_failure(
                            ext_id,
                            type(e).__name__,
                            str(e),
                            "".join(traceback.format_exception(e)),
                        )
                    await ext.stop()
