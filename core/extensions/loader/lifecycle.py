"""Shared lifecycle utilities: state transitions and managed task supervision."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

from core.extensions.contract import ExtensionState

logger = logging.getLogger(__name__)

TaskErrorHandler = Callable[[str, BaseException], Awaitable[None] | None]


class ExtensionStateMachine:
    """Enforces extension runtime transitions: INACTIVE -> ACTIVE -> ERROR."""

    def __init__(self, state: dict[str, ExtensionState]) -> None:
        self._state = state

    def mark_active(self, ext_id: str) -> None:
        current = self._state.get(ext_id)
        if current != ExtensionState.INACTIVE:
            raise ValueError(
                f"Invalid transition for {ext_id}: {current} -> {ExtensionState.ACTIVE}"
            )
        self._state[ext_id] = ExtensionState.ACTIVE

    def mark_error(self, ext_id: str) -> None:
        self._state[ext_id] = ExtensionState.ERROR


class TaskSupervisor:
    """Starts/stops managed asyncio tasks with unified error handling hooks."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def start(
        self,
        name: str,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]],
        on_error: TaskErrorHandler | None = None,
    ) -> asyncio.Task[Any]:
        """Start managed task and attach done callback for failure policy."""
        if name in self._tasks and not self._tasks[name].done():
            raise ValueError(f"Managed task '{name}' is already running")
        task = asyncio.create_task(coro_factory(), name=name)
        self._tasks[name] = task

        def _done(completed: asyncio.Task[Any]) -> None:
            self._tasks.pop(name, None)
            if completed.cancelled():
                return
            exc = completed.exception()
            if exc is None:
                return
            if on_error is None:
                logger.exception("Managed task %s failed: %s", name, exc)
                return
            try:
                result = on_error(name, exc)
                if inspect.isawaitable(result):
                    asyncio.create_task(cast(Coroutine[Any, Any, Any], result))
            except Exception:
                logger.exception("Managed task error handler failed for %s", name)

        task.add_done_callback(_done)
        return task

    async def stop(self, name: str) -> None:
        """Cancel and await task if it is still running."""
        task = self._tasks.pop(name, None)
        if not task:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def stop_all(self) -> None:
        """Cancel and await all managed tasks."""
        for name in list(self._tasks.keys()):
            await self.stop(name)
