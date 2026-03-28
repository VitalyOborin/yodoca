"""Offline simulation helpers for soul drive dynamics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from random import Random

from sandbox.extensions.soul.drives import resolve_phase, tick_homeostasis, transition_phase
from sandbox.extensions.soul.models import HomeostasisState, Phase

DEFAULT_TICK_MINUTES = 5


@dataclass(slots=True)
class SimulationSummary:
    days: int
    ticks: int
    profile: str
    phase_counts: dict[str, int]
    anomaly_counts: dict[str, int]
    event_counts: dict[str, int]
    final_phase: str


def _apply_user_event(
    state: HomeostasisState,
    *,
    kind: str,
) -> HomeostasisState:
    if kind == "inbound":
        return HomeostasisState(
            curiosity=state.curiosity,
            social_hunger=max(0.05, state.social_hunger - 0.25),
            rest_need=min(0.95, state.rest_need + 0.03),
            reflection_need=min(0.95, state.reflection_need + 0.07),
            care_impulse=state.care_impulse,
            overstimulation=min(0.95, state.overstimulation + 0.03),
            current_phase=state.current_phase,
            phase_entered_at=state.phase_entered_at,
            last_tick_at=state.last_tick_at,
        )
    if kind == "burst":
        return HomeostasisState(
            curiosity=state.curiosity,
            social_hunger=max(0.05, state.social_hunger - 0.40),
            rest_need=min(0.95, state.rest_need + 0.10),
            reflection_need=min(0.95, state.reflection_need + 0.18),
            care_impulse=state.care_impulse,
            overstimulation=min(0.95, state.overstimulation + 0.12),
            current_phase=state.current_phase,
            phase_entered_at=state.phase_entered_at,
            last_tick_at=state.last_tick_at,
        )
    return state


def _event_probability(profile: str, hour: int) -> float:
    active_hours = 7 <= hour < 23
    if profile == "silent":
        return 0.01 if active_hours else 0.0
    if profile == "erratic":
        return 0.10 if active_hours else 0.03
    return 0.18 if active_hours else 0.01


def run_simulation(
    *,
    days: int,
    profile: str = "chatty",
    seed: int = 0,
    tick_minutes: int = DEFAULT_TICK_MINUTES,
    start_at: datetime | None = None,
) -> SimulationSummary:
    rng = Random(seed)
    now = start_at or datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    dt = timedelta(minutes=tick_minutes)
    total_ticks = int((timedelta(days=days) / dt))

    state = HomeostasisState(
        phase_entered_at=now,
        last_tick_at=now,
    )
    phase_counts: Counter[str] = Counter()
    anomaly_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()

    previous_phase = state.current_phase
    consecutive_same_phase = 0
    recent_transitions: list[datetime] = []

    for _ in range(total_ticks):
        now = now + dt
        state = tick_homeostasis(state, dt=dt, now=now)

        roll = rng.random()
        probability = _event_probability(profile, now.hour)
        if roll < probability:
            event_kind = "burst" if profile == "erratic" and rng.random() < 0.2 else "inbound"
            state = _apply_user_event(state, kind=event_kind)
            event_counts[event_kind] += 1

        new_phase = resolve_phase(state, now=now)
        if new_phase != state.current_phase:
            recent_transitions.append(now)
            state = transition_phase(state, new_phase, now=now)

        phase_counts[state.current_phase.value] += 1

        if state.current_phase == previous_phase:
            consecutive_same_phase += 1
        else:
            consecutive_same_phase = 0
            previous_phase = state.current_phase

        if consecutive_same_phase * tick_minutes > 8 * 60:
            anomaly_counts["stuck_phase"] += 1
            consecutive_same_phase = 0

        recent_transitions = [ts for ts in recent_transitions if now - ts <= timedelta(hours=1)]
        if len(recent_transitions) >= 8:
            anomaly_counts["oscillation"] += 1
            recent_transitions.clear()

    return SimulationSummary(
        days=days,
        ticks=total_ticks,
        profile=profile,
        phase_counts=dict(phase_counts),
        anomaly_counts=dict(anomaly_counts),
        event_counts=dict(event_counts),
        final_phase=state.current_phase.value,
    )
