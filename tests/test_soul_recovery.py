from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from conftest import FakeSoulContext

from sandbox.extensions.soul.main import SoulExtension
from sandbox.extensions.soul.models import CompanionState, Phase, TemperamentProfile
from sandbox.extensions.soul.recovery import apply_mood_mean_reversion, mood_baseline


@pytest.mark.parametrize(
    ("profile", "expected_min", "expected_max"),
    [
        (TemperamentProfile(), -0.05, 0.05),
        (TemperamentProfile(playfulness=0.9, sociability=0.8), 0.15, 0.40),
        (TemperamentProfile(playfulness=0.1, caution=0.9), -0.30, -0.10),
    ],
    ids=["default-near-zero", "playful-social-positive", "cautious-negative"],
)
def test_mood_baseline_maps_temperament_to_expected_range(
    profile: TemperamentProfile,
    expected_min: float,
    expected_max: float,
) -> None:
    result = mood_baseline(profile)
    assert expected_min <= result <= expected_max, (
        f"mood_baseline={result} outside [{expected_min}, {expected_max}]"
    )


def test_mood_mean_reversion_applies_long_low_mood_floor() -> None:
    state = CompanionState()
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    state.mood = -0.7
    state.recovery.low_mood_since = now - timedelta(hours=73)

    apply_mood_mean_reversion(state, now=now, dt=timedelta(hours=1))

    assert state.mood >= -0.3
    assert state.recovery.last_recovery_reason == "mood_floor"


async def test_no_llm_mode_keeps_runtime_alive_and_marks_degraded(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.homeostasis.last_tick_at = datetime.now(UTC) - timedelta(minutes=30)

    await ext._run_one_tick(now=datetime.now(UTC))
    snapshot = await ext._build_state_snapshot()

    assert snapshot.success is True
    assert snapshot.recovery["llm_degraded"] is True
    assert snapshot.tick_count >= 1


async def test_stuck_phase_recovery_forces_ambient_before_tick(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    ext._state.homeostasis.current_phase = Phase.CURIOUS
    ext._state.homeostasis.curiosity = 0.2
    ext._state.homeostasis.phase_entered_at = now - timedelta(hours=3)
    ext._state.homeostasis.last_tick_at = now - timedelta(minutes=30)

    await ext._run_one_tick(now=now)

    assert ext._state.homeostasis.current_phase is Phase.AMBIENT
    assert ext._state.recovery.last_recovery_reason == "stuck_phase"


async def test_curious_llm_budget_forces_reflective_recovery(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(
        tmp_path,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    assert ext._storage is not None
    ext._state.homeostasis.current_phase = Phase.CURIOUS
    ext._state.recovery.curious_cycle_llm_calls = 9
    now = datetime.now(UTC)
    for index in range(3):
        await ext._storage.append_trace(
            trace_type="interaction",
            phase=Phase.CURIOUS.value,
            content=f"Trace {index}",
            created_at=now - timedelta(minutes=index + 1),
        )

    with patch(
        "sandbox.extensions.soul.exploration_runtime.Runner.run",
        new=AsyncMock(return_value=SimpleNamespace(final_output="One more idea.")),
    ):
        await ext._maybe_explore_internal_space(now)

    assert ext._state.homeostasis.current_phase is Phase.REFLECTIVE
    assert ext._state.recovery.last_recovery_reason == "exploration_runaway"
