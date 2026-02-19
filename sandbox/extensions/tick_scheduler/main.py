"""Tick scheduler extension: SchedulerProvider that runs every minute and sends a notification."""

from datetime import datetime
from typing import Any


class TickSchedulerExtension:
    """Extension + SchedulerProvider: cron every minute, execute() returns text for notify_user."""

    async def initialize(self, context: Any) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return True

    def get_schedule(self) -> str:
        return "* * * * *"  # every minute

    async def execute(self) -> dict[str, Any] | None:
        return {"text": f"tick at {datetime.now().strftime('%H:%M:%S')}"}
