"""Offline simulation helpers for soul drive dynamics.

Run from CLI:  python -m sandbox.extensions.soul.simulator --days 7 --profile chatty
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from random import Random

from sandbox.extensions.soul.drives import (
    resolve_phase,
    tick_homeostasis,
    transition_phase,
)
from sandbox.extensions.soul.models import HomeostasisState

DEFAULT_TICK_MINUTES = 5
DRIVE_NAMES = (
    "curiosity",
    "social_hunger",
    "rest_need",
    "reflection_need",
    "care_impulse",
    "overstimulation",
)


@dataclass(slots=True)
class SimulationSummary:
    days: int
    ticks: int
    profile: str
    phase_counts: dict[str, int]
    anomaly_counts: dict[str, int]
    event_counts: dict[str, int]
    final_phase: str
    min_drives: dict[str, float] = field(default_factory=dict)
    max_drives: dict[str, float] = field(default_factory=dict)


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
    if profile == "burst":
        return 0.95 if active_hours else 0.0
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
    now = start_at or datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
    dt = timedelta(minutes=tick_minutes)
    total_ticks = int(timedelta(days=days) / dt)

    state = HomeostasisState(
        phase_entered_at=now,
        last_tick_at=now,
    )
    phase_counts: Counter[str] = Counter()
    anomaly_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    min_drives = {name: 1.0 for name in DRIVE_NAMES}
    max_drives = {name: 0.0 for name in DRIVE_NAMES}

    previous_phase = state.current_phase
    consecutive_same_phase = 0
    recent_transitions: list[datetime] = []

    for _ in range(total_ticks):
        now = now + dt
        state = tick_homeostasis(state, dt=dt, now=now)

        roll = rng.random()
        probability = _event_probability(profile, now.hour)
        if roll < probability:
            use_burst = profile in ("erratic", "burst") and rng.random() < (
                0.5 if profile == "burst" else 0.2
            )
            event_kind = "burst" if use_burst else "inbound"
            state = _apply_user_event(state, kind=event_kind)
            event_counts[event_kind] += 1

        new_phase = resolve_phase(state, now=now)
        if new_phase != state.current_phase:
            recent_transitions.append(now)
            state = transition_phase(state, new_phase, now=now)

        phase_counts[state.current_phase.value] += 1

        for name in DRIVE_NAMES:
            val = getattr(state, name)
            if val < min_drives[name]:
                min_drives[name] = val
            if val > max_drives[name]:
                max_drives[name] = val

        if state.current_phase == previous_phase:
            consecutive_same_phase += 1
        else:
            consecutive_same_phase = 0
            previous_phase = state.current_phase

        if consecutive_same_phase * tick_minutes > 8 * 60:
            anomaly_counts["stuck_phase"] += 1
            consecutive_same_phase = 0

        recent_transitions = [
            ts for ts in recent_transitions if now - ts <= timedelta(hours=1)
        ]
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
        min_drives=min_drives,
        max_drives=max_drives,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Soul drive dynamics simulator")
    parser.add_argument(
        "--days", type=int, default=7, help="Days to simulate (default: 7)"
    )
    parser.add_argument(
        "--profile",
        default="chatty",
        choices=["chatty", "silent", "erratic", "burst"],
        help="User interaction profile (default: chatty)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed for reproducibility (default: 0)",
    )
    parser.add_argument(
        "--tick",
        type=int,
        default=DEFAULT_TICK_MINUTES,
        help=f"Tick interval in minutes (default: {DEFAULT_TICK_MINUTES})",
    )
    args = parser.parse_args(argv)

    summary = run_simulation(
        days=args.days, profile=args.profile, seed=args.seed, tick_minutes=args.tick
    )

    print(f"Simulation: {summary.days}d, profile={summary.profile}, seed={args.seed}")
    print(f"Ticks: {summary.ticks}, final phase: {summary.final_phase}")
    print()

    print("Phase distribution:")
    total = sum(summary.phase_counts.values())
    for phase, count in sorted(summary.phase_counts.items(), key=lambda x: -x[1]):
        print(f"  {phase:12s}  {count:6d} ticks  ({count / total * 100:5.1f}%)")
    print()

    print("Drive bounds (min / max):")
    for name in DRIVE_NAMES:
        lo = summary.min_drives.get(name, 0.0)
        hi = summary.max_drives.get(name, 0.0)
        print(f"  {name:20s}  {lo:.3f} / {hi:.3f}")
    print()

    if summary.event_counts:
        print("User events:")
        for kind, count in sorted(summary.event_counts.items()):
            print(f"  {kind:10s}  {count}")
        print()

    if summary.anomaly_counts:
        print("ANOMALIES:")
        for kind, count in sorted(summary.anomaly_counts.items()):
            print(f"  {kind:15s}  {count}")
    else:
        print("No anomalies detected.")


if __name__ == "__main__":
    main()
