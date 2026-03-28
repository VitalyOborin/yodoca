"""Accelerated Stage 2 gate runner for controlled initiative."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from sandbox.extensions.soul.main import SoulExtension
from sandbox.extensions.soul.models import Phase


class GateContext:
    def __init__(self, root: Path) -> None:
        self._config = {
            "tick_interval_seconds": 30,
            "persist_interval_seconds": 60,
        }
        self.data_dir = root / "soul-stage2-gate"
        self.extension_dir = Path("sandbox/extensions/soul")
        self.logger = logging.getLogger("soul.stage2.gate")
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[tuple[str, str | None]] = []

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    async def emit(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))

    async def notify_user(self, text: str, channel_id: str | None = None) -> None:
        self.notifications.append((text, channel_id))

    def subscribe(self, event: str, handler: Any) -> None:
        del event, handler

    def subscribe_event(self, topic: str, handler: Any) -> None:
        del topic, handler


async def main() -> None:
    root = Path(mkdtemp(prefix="soul-stage2-gate-"))
    context = GateContext(root)
    ext = SoulExtension()
    await ext.initialize(context)
    await ext.start()

    assert ext._state is not None
    noon = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    ext._state.user_presence.estimated_availability = 0.8
    ext._state.homeostasis.current_phase = Phase.CURIOUS
    ext._state.homeostasis.social_hunger = 0.9
    ext._state.homeostasis.last_tick_at = noon - timedelta(hours=4)
    await ext._run_one_tick(now=noon)
    await ext._on_user_message({"text": "hi", "channel": object()})

    next_day = datetime(2026, 3, 30, 12, 0, tzinfo=UTC)
    ext._state.initiative.budget.last_reset_at = noon
    ext._state.initiative.budget.used_today = 0
    ext._state.initiative.cooldown_until = None
    ext._state.user_presence.estimated_availability = 0.8
    ext._state.homeostasis.social_hunger = 0.95
    ext._state.homeostasis.last_tick_at = next_day - timedelta(hours=4)
    await ext._run_one_tick(now=next_day)
    await ext._run_one_tick(now=next_day + timedelta(minutes=61))

    night = datetime(2026, 3, 30, 23, 0, tzinfo=UTC)
    ext._state.initiative.budget.used_today = 0
    ext._state.initiative.cooldown_until = None
    ext._state.user_presence.estimated_availability = 0.8
    ext._state.homeostasis.social_hunger = 0.95
    ext._state.homeostasis.last_tick_at = night - timedelta(hours=4)
    before_night = len(context.notifications)
    await ext._run_one_tick(now=night)

    snapshot = ext._build_state_snapshot()

    print("Stage 2 accelerated gate")
    print(f"Artifacts: {root}")
    print(f"Notifications sent: {len(context.notifications)}")
    print(f"Night blocked: {len(context.notifications) == before_night}")
    print(
        "Last outreach result: "
        f"{snapshot.initiative.get('last_outreach_result')}"
    )
    print(f"Cooldown until: {snapshot.initiative.get('cooldown_until')}")
    print(f"Initiative snapshot: {snapshot.initiative}")

    await ext.stop()
    await ext.destroy()


if __name__ == "__main__":
    asyncio.run(main())
