"""SchedulerManager: cron-driven periodic task execution for SchedulerProvider extensions."""

import asyncio
import logging
import time

from croniter import croniter

from core.extensions.contract import ExtensionState, SchedulerProvider
from core.extensions.manifest import ExtensionManifest
from core.extensions.router import MessageRouter

logger = logging.getLogger(__name__)

_CRON_TICK_SEC = 60


class SchedulerManager:
    """Cron-driven periodic task execution. Reads shared state; notifies via router."""

    def __init__(
        self,
        state: dict[str, ExtensionState],
        router: MessageRouter,
    ) -> None:
        self._state = state
        self._router = router
        self._schedulers: dict[str, SchedulerProvider] = {}
        self._manifests: dict[str, ExtensionManifest] = {}
        self._task_next: dict[str, float] = {}
        self._task: asyncio.Task[object] | None = None

    def register(
        self,
        ext_id: str,
        ext: SchedulerProvider,
        manifest: ExtensionManifest,
    ) -> None:
        """Register a SchedulerProvider. Called from Loader.detect_and_wire_all."""
        self._schedulers[ext_id] = ext
        self._manifests[ext_id] = manifest

    def start(self) -> None:
        """Initialize cron times and start the dispatch loop."""
        now = time.time()
        for ext_id, manifest in self._manifests.items():
            if not manifest.schedules:
                logger.warning(
                    "SchedulerProvider %s has no schedules in manifest", ext_id
                )
                continue
            for entry in manifest.schedules:
                key = f"{ext_id}::{entry.task_name}"
                try:
                    c = croniter(entry.cron, now)
                    self._task_next[key] = c.get_next(float)
                except Exception as e:
                    logger.warning(
                        "Invalid cron '%s' for %s/%s: %s",
                        entry.cron,
                        ext_id,
                        entry.task_name,
                        e,
                    )
                    self._task_next[key] = now + 86400
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel and await the cron loop task."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        """Every minute evaluate schedules; call execute_task on match."""
        while True:
            await asyncio.sleep(_CRON_TICK_SEC)
            now = time.time()
            for ext_id, ext in list(self._schedulers.items()):
                if self._state.get(ext_id) != ExtensionState.ACTIVE:
                    continue
                manifest = self._manifests.get(ext_id)
                if not manifest or not manifest.schedules:
                    continue
                for entry in manifest.schedules:
                    key = f"{ext_id}::{entry.task_name}"
                    next_run = self._task_next.get(key, 0)
                    if now < next_run:
                        continue
                    try:
                        result = await ext.execute_task(entry.task_name)
                        self._task_next[key] = croniter(
                            entry.cron, next_run
                        ).get_next(float)
                        if (
                            result
                            and isinstance(result, dict)
                            and "text" in result
                        ):
                            await self._router.notify_user(result["text"])
                    except Exception as e:
                        logger.exception(
                            "Scheduled task %s/%s failed: %s",
                            ext_id,
                            entry.task_name,
                            e,
                        )
