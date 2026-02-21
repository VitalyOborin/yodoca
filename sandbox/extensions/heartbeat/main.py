"""Heartbeat extension: periodically wakes the Orchestrator for proactive work.

Uses SchedulerProvider + Core Cron Loop (Loader._cron_loop). No ServiceProvider.
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT = (
    "Check if there's anything proactive to do. If nothing urgent, acknowledge briefly."
)


class HeartbeatExtension:
    """SchedulerProvider: emits system.agent.background via Core Cron Loop."""

    def __init__(self) -> None:
        self._ctx: "ExtensionContext | None" = None

    async def initialize(self, context: "ExtensionContext") -> None:
        self._ctx = context

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._ctx = None

    def health_check(self) -> bool:
        return self._ctx is not None

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        """Called by Loader._cron_loop when schedule fires. Emits system.agent.background."""
        if task_name != "emit_heartbeat":
            logger.warning("Unknown heartbeat task: %s", task_name)
            return None

        ctx = self._ctx
        if not ctx:
            return None

        prompt = ctx.get_config("prompt", _DEFAULT_PROMPT)
        if isinstance(prompt, str):
            prompt = prompt.strip()

        await ctx.request_agent_background(prompt)
        logger.debug("Heartbeat: emitted system.agent.background")
        return None  # No user notification for background task
