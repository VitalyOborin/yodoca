"""Agent lifecycle: periodic cleanup of expired dynamic agents."""

import asyncio
import logging

from core.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)


def start_lifecycle_loop(
    registry: AgentRegistry, interval_seconds: float = 60.0
) -> asyncio.Task[None]:
    """Start a background task that periodically cleans up expired dynamic agents.

    Returns the asyncio task. Cancel it to stop the loop.
    """

    async def loop() -> None:
        while True:
            try:
                removed = registry.cleanup_expired()
                if removed > 0:
                    logger.info(
                        "Lifecycle: removed %d expired dynamic agent(s)", removed
                    )
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Lifecycle cleanup failed")
                await asyncio.sleep(interval_seconds)

    return asyncio.create_task(loop())
