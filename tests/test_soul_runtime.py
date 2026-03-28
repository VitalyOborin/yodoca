import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sandbox.extensions.soul.main import SoulExtension
from sandbox.extensions.soul.models import Phase, PresenceState


class FakeContext:
    def __init__(self, tmp_path: Path, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self.data_dir = tmp_path / "soul-data"
        self.extension_dir = Path("sandbox/extensions/soul")
        self.logger = logging.getLogger("test.soul")
        self.events: list[tuple[str, dict[str, Any]]] = []

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    async def emit(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))


async def test_inner_tick_emits_phase_and_presence_events(tmp_path: Path) -> None:
    context = FakeContext(
        tmp_path,
        {
            "tick_interval_seconds": 30,
            "persist_interval_seconds": 300,
        },
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.presence = PresenceState.SILENT
    ext._state.homeostasis.current_phase = Phase.AMBIENT
    ext._state.homeostasis.curiosity = 0.9
    ext._state.homeostasis.phase_entered_at = datetime.now(UTC) - timedelta(minutes=10)
    ext._state.homeostasis.last_tick_at = datetime.now(UTC) - timedelta(minutes=10)

    tick_now = datetime.now(UTC)
    await ext._run_one_tick(now=tick_now)

    assert ext._state.homeostasis.current_phase is Phase.CURIOUS
    assert ext._state.presence is PresenceState.PLAYFUL
    assert [topic for topic, _ in context.events] == [
        "companion.phase.changed",
        "companion.presence.updated",
    ]

    restored = await ext._storage.load_state() if ext._storage else None
    assert restored is not None
    assert restored.homeostasis.current_phase is Phase.CURIOUS
    assert restored.tick_count == 1


async def test_run_background_advances_ticks_until_stopped(tmp_path: Path) -> None:
    context = FakeContext(
        tmp_path,
        {
            "tick_interval_seconds": 0.01,
            "persist_interval_seconds": 0.01,
        },
    )
    ext = SoulExtension()
    await ext.initialize(context)
    await ext.start()

    task = asyncio.create_task(ext.run_background())
    await asyncio.sleep(0.05)
    await ext.stop()
    await asyncio.wait_for(task, timeout=0.2)

    assert ext._state is not None
    assert ext._state.tick_count > 0
    assert ext._last_tick_finished_at is not None
    assert ext.health_check() is True


async def test_health_check_fails_for_stale_heartbeat(tmp_path: Path) -> None:
    context = FakeContext(
        tmp_path,
        {
            "tick_interval_seconds": 10,
            "persist_interval_seconds": 60,
        },
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._started = True
    ext._last_tick_started_at = datetime.now(UTC) - timedelta(seconds=25)

    assert ext.health_check() is False


async def test_health_check_uses_recent_state_tick_when_loop_idle(tmp_path: Path) -> None:
    context = FakeContext(
        tmp_path,
        {
            "tick_interval_seconds": 10,
            "persist_interval_seconds": 60,
        },
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._last_tick_started_at = None
    ext._state.homeostasis.last_tick_at = datetime.now(UTC) - timedelta(seconds=5)

    assert ext.health_check() is True
