"""Pure drive dynamics for the soul companion runtime."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sandbox.extensions.soul.models import HomeostasisState, Phase

DRIVE_GROWTH_RATE = 0.008
HYSTERESIS_MARGIN = 0.15
MIN_DWELL_TIME = timedelta(minutes=10)
MIN_DRIVE_VALUE = 0.05
MAX_DRIVE_VALUE = 0.95
OVERSTIMULATION_REST_THRESHOLD = 0.8

COUPLING_MATRIX: dict[Phase, dict[str, float]] = {
    Phase.CURIOUS: {
        "social_hunger": 0.2,
        "rest_need": 0.3,
        "reflection_need": 0.0,
        "care_impulse": 0.0,
        "overstimulation": 0.1,
    },
    Phase.SOCIAL: {
        "curiosity": -0.5,
        "rest_need": 0.4,
        "reflection_need": 0.6,
        "care_impulse": 0.0,
        "overstimulation": 0.3,
    },
    Phase.REFLECTIVE: {
        "curiosity": -0.3,
        "social_hunger": -0.2,
        "rest_need": 0.2,
        "care_impulse": 0.0,
        "overstimulation": 0.0,
    },
    Phase.RESTING: {
        "curiosity": -0.8,
        "social_hunger": -0.1,
        "reflection_need": -0.3,
        "care_impulse": 0.0,
        "overstimulation": -0.5,
    },
    Phase.CARE: {
        "curiosity": 0.0,
        "social_hunger": 0.3,
        "rest_need": 0.2,
        "reflection_need": 0.0,
        "overstimulation": 0.2,
    },
    Phase.AMBIENT: {},
}

PHASE_SATIATION: dict[Phase, dict[str, float]] = {
    Phase.CURIOUS: {"curiosity": 0.010},
    Phase.SOCIAL: {"social_hunger": 0.014},
    Phase.REFLECTIVE: {"reflection_need": 0.012},
    Phase.RESTING: {"rest_need": 0.020, "overstimulation": 0.025},
    Phase.CARE: {"care_impulse": 0.012},
    Phase.AMBIENT: {"overstimulation": 0.003},
}


def circadian_modifier(hour: int) -> dict[str, float]:
    if 6 <= hour < 10:
        return {"curiosity": 1.3, "social_hunger": 0.7, "rest_need": 0.5}
    if 10 <= hour < 18:
        return {"curiosity": 1.0, "social_hunger": 1.0, "rest_need": 0.8}
    if 18 <= hour < 22:
        return {"curiosity": 0.8, "social_hunger": 1.3, "rest_need": 1.0}
    return {"curiosity": 0.3, "social_hunger": 0.2, "rest_need": 2.0}


def clamp_drive(value: float) -> float:
    return max(MIN_DRIVE_VALUE, min(MAX_DRIVE_VALUE, value))


def _phase_drive_scores(state: HomeostasisState) -> dict[Phase, float]:
    return {
        Phase.CURIOUS: state.curiosity,
        Phase.SOCIAL: state.social_hunger,
        Phase.REFLECTIVE: state.reflection_need,
        Phase.RESTING: max(state.rest_need, state.overstimulation),
        Phase.CARE: state.care_impulse,
    }


def resolve_phase(
    state: HomeostasisState,
    *,
    now: datetime | None = None,
    hysteresis_margin: float = HYSTERESIS_MARGIN,
    min_dwell_time: timedelta = MIN_DWELL_TIME,
) -> Phase:
    now = now or datetime.now(UTC)

    if state.overstimulation >= OVERSTIMULATION_REST_THRESHOLD:
        return Phase.RESTING

    if now - state.phase_entered_at < min_dwell_time:
        return state.current_phase

    scores = _phase_drive_scores(state)
    top_phase, top_score = max(scores.items(), key=lambda item: item[1])

    if top_score < 0.35:
        return Phase.AMBIENT

    if state.current_phase is not Phase.AMBIENT:
        current_score = scores.get(state.current_phase, 0.0)
        if top_phase is state.current_phase:
            return state.current_phase
        if top_score - current_score < hysteresis_margin:
            return state.current_phase

    return top_phase


def tick_homeostasis(
    state: HomeostasisState,
    *,
    dt: timedelta,
    now: datetime | None = None,
) -> HomeostasisState:
    now = now or datetime.now(UTC)
    minutes = max(dt.total_seconds() / 60.0, 0.0)
    if minutes == 0:
        return replace(state, last_tick_at=now)

    coupling = COUPLING_MATRIX.get(state.current_phase, {})
    circadian = circadian_modifier(now.hour)

    def grow(name: str, current: float) -> float:
        modifier = circadian.get(name, 1.0)
        modifier *= 1.0 + coupling.get(name, 0.0)
        return clamp_drive(current + (DRIVE_GROWTH_RATE * modifier * minutes))

    values = {
        "curiosity": grow("curiosity", state.curiosity),
        "social_hunger": grow("social_hunger", state.social_hunger),
        "rest_need": grow("rest_need", state.rest_need),
        "reflection_need": grow("reflection_need", state.reflection_need),
        "care_impulse": grow("care_impulse", state.care_impulse),
        "overstimulation": grow("overstimulation", state.overstimulation),
    }
    for drive_name, satiation_rate in PHASE_SATIATION.get(
        state.current_phase, {}
    ).items():
        values[drive_name] = clamp_drive(
            values[drive_name] - (satiation_rate * minutes)
        )

    return HomeostasisState(
        curiosity=values["curiosity"],
        social_hunger=values["social_hunger"],
        rest_need=values["rest_need"],
        reflection_need=values["reflection_need"],
        care_impulse=values["care_impulse"],
        overstimulation=values["overstimulation"],
        current_phase=state.current_phase,
        phase_entered_at=state.phase_entered_at,
        last_tick_at=now,
    )


def transition_phase(
    state: HomeostasisState,
    new_phase: Phase,
    *,
    now: datetime | None = None,
) -> HomeostasisState:
    now = now or datetime.now(UTC)
    if new_phase is state.current_phase:
        return replace(state, last_tick_at=now)
    return replace(
        state,
        current_phase=new_phase,
        phase_entered_at=now,
        last_tick_at=now,
    )
