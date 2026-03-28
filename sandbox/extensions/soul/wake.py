"""Wake-up protocol for restoring soul state after downtime."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

from sandbox.extensions.soul.drives import tick_homeostasis, transition_phase
from sandbox.extensions.soul.models import CompanionState, HomeostasisState, Phase


class WakeMode(str, Enum):
    SEAMLESS = "SEAMLESS"
    SOFT = "SOFT"
    NATURAL = "NATURAL"
    LONG_ABSENCE = "LONG_ABSENCE"


@dataclass(slots=True)
class WakeUpResult:
    mode: WakeMode
    gap: timedelta
    state: CompanionState


def restore_after_gap(
    state: CompanionState,
    *,
    now: datetime | None = None,
) -> WakeUpResult:
    now = now or datetime.now(timezone.utc)
    gap = now - state.homeostasis.last_tick_at

    if gap < timedelta(minutes=5):
        seamless = replace(
            state,
            homeostasis=replace(state.homeostasis, last_tick_at=now),
        )
        return WakeUpResult(WakeMode.SEAMLESS, gap, seamless)

    if gap < timedelta(hours=1):
        soft = _soft_wake(state, gap=gap, now=now)
        return WakeUpResult(WakeMode.SOFT, gap, soft)

    if gap < timedelta(hours=12):
        natural = _natural_wake(state, gap=gap, now=now)
        return WakeUpResult(WakeMode.NATURAL, gap, natural)

    long_absence = _long_absence_wake(state, now=now)
    return WakeUpResult(WakeMode.LONG_ABSENCE, gap, long_absence)


def _soft_wake(
    state: CompanionState,
    *,
    gap: timedelta,
    now: datetime,
) -> CompanionState:
    advanced = tick_homeostasis(state.homeostasis, dt=gap, now=now)
    advanced = transition_phase(advanced, Phase.AMBIENT, now=now)
    advanced = replace(
        advanced,
        rest_need=min(advanced.rest_need, 0.10),
    )
    return replace(state, homeostasis=advanced)


def _natural_wake(
    state: CompanionState,
    *,
    gap: timedelta,
    now: datetime,
) -> CompanionState:
    advanced = tick_homeostasis(state.homeostasis, dt=gap, now=now)
    advanced = transition_phase(advanced, Phase.AMBIENT, now=now)
    advanced = replace(
        advanced,
        rest_need=0.05,
    )
    return replace(state, homeostasis=advanced)


def _long_absence_wake(
    state: CompanionState,
    *,
    now: datetime,
) -> CompanionState:
    baseline = HomeostasisState(
        current_phase=Phase.AMBIENT,
        phase_entered_at=now,
        last_tick_at=now,
    )
    return CompanionState(
        version=state.version,
        homeostasis=baseline,
        presence=state.presence,
        mood=state.mood * 0.5,
        tick_count=state.tick_count,
        temperament=state.temperament,
    )
