"""Accelerated Stage 1 soak runner for the soul runtime."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from sandbox.extensions.soul.main import SoulExtension


class SoakContext:
    def __init__(self, root: Path) -> None:
        self._config = {
            "tick_interval_seconds": 30,
            "persist_interval_seconds": 60,
        }
        self.data_dir = root / "soul-soak-data"
        self.extension_dir = Path("sandbox/extensions/soul")
        self.logger = logging.getLogger("soul.soak")
        self.events: list[tuple[str, dict[str, Any]]] = []

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    async def emit(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))

    def subscribe(self, event: str, handler: Any) -> None:
        del event, handler

    def subscribe_event(self, topic: str, handler: Any) -> None:
        del topic, handler


async def main() -> None:
    root = Path(mkdtemp(prefix="soul-stage1-soak-"))
    context = SoakContext(root)
    ext = SoulExtension()
    await ext.initialize(context)
    await ext.start()

    start = datetime(2026, 3, 29, 8, 0, tzinfo=UTC)
    assert ext._state is not None
    ext._state.homeostasis.last_tick_at = start - timedelta(minutes=30)

    for _ in range(6):
        await ext._on_user_message(
            {
                "text": "Can we think through the design tradeoffs together right now?",
                "channel": object(),
            }
        )
    morning_context = await ext.get_context("hello", object())
    for step in range(1, 25):
        now = start + timedelta(minutes=30 * step)
        await ext._run_one_tick(now=now)
    for _ in range(6):
        await ext._on_user_message({"text": "...", "channel": object()})
    evening_context = await ext.get_context("hello", object())
    snapshot = ext._build_state_snapshot()

    db_path = context.data_dir / "soul.db"
    with sqlite3.connect(db_path) as conn:
        phase_transitions = conn.execute(
            "SELECT COUNT(*) FROM traces WHERE trace_type = 'phase_transition'"
        ).fetchone()[0]

    restarted = SoulExtension()
    restarted_context = SoakContext(root)
    await restarted.initialize(restarted_context)

    print("Stage 1 accelerated soak")
    print(f"Artifacts: {root}")
    print(f"Health: {ext.health_check()}")
    print(f"Tick count: {snapshot.tick_count}")
    print(f"Phase transitions: {phase_transitions}")
    print(f"Morning context: {morning_context}")
    print(f"Evening context: {evening_context}")
    print(
        "Restart restored tick count: "
        f"{restarted._state.tick_count if restarted._state else 'n/a'}"
    )

    await ext.stop()
    await ext.destroy()
    await restarted.destroy()


if __name__ == "__main__":
    asyncio.run(main())
