from datetime import datetime, timedelta, timezone

from sandbox.extensions.soul.models import CompanionState, Phase
from sandbox.extensions.soul.wake import WakeMode, restore_after_gap


def test_seamless_resume_updates_last_tick_only() -> None:
    now = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
    state = CompanionState()
    state.homeostasis.last_tick_at = now - timedelta(minutes=2)

    result = restore_after_gap(state, now=now)

    assert result.mode is WakeMode.SEAMLESS
    assert result.state.homeostasis.last_tick_at == now


def test_soft_wake_caps_rest_need_and_returns_ambient() -> None:
    now = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
    state = CompanionState()
    state.homeostasis.last_tick_at = now - timedelta(minutes=20)
    state.homeostasis.current_phase = Phase.CURIOUS

    result = restore_after_gap(state, now=now)

    assert result.mode is WakeMode.SOFT
    assert result.state.homeostasis.current_phase is Phase.AMBIENT
    assert result.state.homeostasis.rest_need <= 0.10


def test_natural_wake_resets_rest_need() -> None:
    now = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
    state = CompanionState()
    state.homeostasis.last_tick_at = now - timedelta(hours=3)

    result = restore_after_gap(state, now=now)

    assert result.mode is WakeMode.NATURAL
    assert result.state.homeostasis.rest_need == 0.05


def test_long_absence_resets_to_baseline() -> None:
    now = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
    state = CompanionState()
    state.mood = 0.8
    state.tick_count = 77
    state.homeostasis.last_tick_at = now - timedelta(days=2)
    state.homeostasis.current_phase = Phase.SOCIAL

    result = restore_after_gap(state, now=now)

    assert result.mode is WakeMode.LONG_ABSENCE
    assert result.state.homeostasis.current_phase is Phase.AMBIENT
    assert result.state.homeostasis.last_tick_at == now
    assert result.state.tick_count == 77
    assert result.state.mood == 0.4
