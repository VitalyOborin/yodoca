import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sandbox.extensions.soul.main import SoulExtension


class FakeContext:
    def __init__(self, tmp_path: Path, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.data_dir = tmp_path / "soul-soak-data"
        self.extension_dir = Path("sandbox/extensions/soul")
        self.logger = logging.getLogger("test.soul.soak")
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.router_subscriptions: dict[str, Any] = {}
        self.bus_subscriptions: dict[str, Any] = {}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    async def emit(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))

    def subscribe(self, event: str, handler: Any) -> None:
        self.router_subscriptions[event] = handler

    def subscribe_event(self, topic: str, handler: Any) -> None:
        self.bus_subscriptions[topic] = handler


async def test_stage1_accelerated_soak_proves_life_cycle(tmp_path: Path) -> None:
    config = {
        "tick_interval_seconds": 30,
        "persist_interval_seconds": 60,
    }
    context = FakeContext(tmp_path, config)
    ext = SoulExtension()
    await ext.initialize(context)
    await ext.start()

    assert ext.health_check() is True
    assert ext._state is not None

    start = datetime(2026, 3, 29, 8, 0, tzinfo=UTC)
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

    assert morning_context is not None
    assert evening_context is not None
    assert morning_context != evening_context
    assert snapshot.success is True
    assert snapshot.tick_count >= 24

    db_path = context.data_dir / "soul.db"
    with sqlite3.connect(db_path) as conn:
        phase_transitions = conn.execute(
            "SELECT COUNT(*) FROM traces WHERE trace_type = 'phase_transition'"
        ).fetchone()[0]

    assert phase_transitions >= 1

    restarted_context = FakeContext(tmp_path, config)
    restarted = SoulExtension()
    await restarted.initialize(restarted_context)

    assert restarted._state is not None
    assert restarted._state.tick_count >= snapshot.tick_count
    assert restarted.health_check() is True
