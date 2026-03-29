"""Failure recovery helpers for the soul runtime."""

from __future__ import annotations

from datetime import datetime, timedelta

from sandbox.extensions.soul.drives import transition_phase
from sandbox.extensions.soul.models import CompanionState, Phase, TemperamentProfile

CURIOUS_LLM_CALL_LIMIT = 10
LOW_MOOD_FLOOR = -0.3
MAX_DWELL_TIME: dict[Phase, timedelta] = {
    Phase.CURIOUS: timedelta(hours=2),
    Phase.SOCIAL: timedelta(hours=1),
    Phase.REFLECTIVE: timedelta(hours=3),
    Phase.RESTING: timedelta(hours=8),
    Phase.CARE: timedelta(hours=2),
}


def mood_baseline(temperament: TemperamentProfile) -> float:
    baseline = (
        ((temperament.playfulness - 0.5) * 0.6)
        + ((temperament.sociability - 0.5) * 0.3)
        + ((temperament.depth - 0.5) * 0.1)
        - ((temperament.caution - 0.5) * 0.2)
    )
    return max(LOW_MOOD_FLOOR, min(0.4, round(baseline, 4)))


def apply_mood_mean_reversion(
    state: CompanionState,
    *,
    now: datetime,
    dt: timedelta,
) -> None:
    days = max(dt.total_seconds() / 86400.0, 0.0)
    baseline = mood_baseline(state.temperament)

    if state.mood < -0.5:
        if state.recovery.low_mood_since is None:
            state.recovery.low_mood_since = now
    else:
        state.recovery.low_mood_since = None

    if state.mood < baseline:
        state.mood = min(state.mood + (0.02 * days), baseline)
    elif state.mood > baseline:
        state.mood = max(state.mood - (0.01 * days), baseline)

    if (
        state.recovery.low_mood_since is not None
        and now - state.recovery.low_mood_since >= timedelta(hours=72)
    ):
        state.mood = max(state.mood, LOW_MOOD_FLOOR)
        state.recovery.last_recovery_at = now
        state.recovery.last_recovery_reason = "mood_floor"


def should_reset_stuck_phase(state: CompanionState, *, now: datetime) -> bool:
    phase = state.homeostasis.current_phase
    if phase is Phase.AMBIENT:
        return False
    limit = MAX_DWELL_TIME.get(phase)
    if limit is None:
        return False
    return now - state.homeostasis.phase_entered_at > limit


def reset_stuck_phase(state: CompanionState, *, now: datetime) -> Phase:
    previous = state.homeostasis.current_phase
    state.homeostasis = transition_phase(state.homeostasis, Phase.AMBIENT, now=now)
    state.recovery.last_recovery_at = now
    state.recovery.last_recovery_reason = "stuck_phase"
    state.recovery.curious_cycle_llm_calls = 0
    return previous


def set_llm_degraded(state: CompanionState, *, degraded: bool) -> None:
    state.recovery.llm_degraded = degraded


def can_use_curious_llm(state: CompanionState) -> bool:
    if state.homeostasis.current_phase is not Phase.CURIOUS:
        return True
    return state.recovery.curious_cycle_llm_calls < CURIOUS_LLM_CALL_LIMIT


def record_curious_llm_call(state: CompanionState) -> int:
    if state.homeostasis.current_phase is not Phase.CURIOUS:
        return 0
    state.recovery.curious_cycle_llm_calls += 1
    return state.recovery.curious_cycle_llm_calls


def reset_curious_cycle_budget(
    state: CompanionState,
    *,
    previous_phase: Phase | None,
) -> None:
    if previous_phase is Phase.CURIOUS and state.homeostasis.current_phase is not Phase.CURIOUS:
        state.recovery.curious_cycle_llm_calls = 0


def force_runaway_recovery(state: CompanionState, *, now: datetime) -> Phase:
    previous = state.homeostasis.current_phase
    state.homeostasis = transition_phase(state.homeostasis, Phase.REFLECTIVE, now=now)
    state.recovery.last_recovery_at = now
    state.recovery.last_recovery_reason = "exploration_runaway"
    state.recovery.curious_cycle_llm_calls = CURIOUS_LLM_CALL_LIMIT
    return previous
