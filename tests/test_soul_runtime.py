import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import FakeSoulContext

from sandbox.extensions.soul.main import SoulExtension
from sandbox.extensions.soul.models import Phase, PresenceState


async def test_inner_tick_emits_phase_and_presence_events(tmp_path: Path) -> None:
    context = FakeSoulContext(
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


async def test_initialize_wires_router_and_event_bus_subscriptions(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()

    await ext.initialize(context)

    assert "user_message" in context.router_subscriptions
    assert "agent_response" in context.router_subscriptions
    assert "thread.completed" in context.bus_subscriptions


async def test_run_background_advances_ticks_until_stopped(tmp_path: Path) -> None:
    context = FakeSoulContext(
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
    context = FakeSoulContext(
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


async def test_health_check_uses_recent_state_tick_when_loop_idle(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(
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


async def test_user_message_updates_perception_and_social_hunger(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.homeostasis.social_hunger = 0.6
    ext._last_agent_response_at = datetime.now(UTC) - timedelta(minutes=20)

    await ext._on_user_message({"text": "ok...", "channel": object()})

    assert ext._state.homeostasis.social_hunger < 0.6
    assert ext._state.perception.withdrawal_signal > 0.1
    assert ext._last_user_message_at is not None


async def test_context_provider_returns_compact_note(tmp_path: Path) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.perception.fatigue_signal = 0.7
    ext._state.presence = PresenceState.WARM
    ext._state.mood = 0.4

    result = await ext.get_context("hello", object())

    assert result is not None
    assert "phase:" in result
    assert "presence:" in result
    assert "mood: warm" in result
    assert "User seems tired; be brief and present." in result
    assert len(result.split()) < 80


async def test_tool_snapshot_exposes_runtime_state(tmp_path: Path) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.homeostasis.current_phase = Phase.REFLECTIVE
    ext._state.homeostasis.phase_entered_at = datetime.now(UTC) - timedelta(minutes=7)
    ext._state.tick_count = 5
    ext._state.mood = 0.2

    snapshot = ext._build_state_snapshot()

    assert snapshot.success is True
    assert snapshot.phase == "REFLECTIVE"
    assert snapshot.tick_count == 5
    assert snapshot.time_in_phase_seconds >= 420
    assert "curiosity" in snapshot.drives
    assert "daily_budget" in snapshot.initiative
    assert len(ext.get_tools()) == 1
