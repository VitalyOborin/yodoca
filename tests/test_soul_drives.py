from datetime import UTC, datetime, timedelta

from sandbox.extensions.soul.drives import (
    HYSTERESIS_MARGIN,
    MIN_DRIVE_VALUE,
    circadian_modifier,
    resolve_phase,
    tick_homeostasis,
    transition_phase,
)
from sandbox.extensions.soul.models import HomeostasisState, Phase


def test_tick_homeostasis_clamps_values() -> None:
    state = HomeostasisState(
        curiosity=0.94,
        social_hunger=0.94,
        rest_need=0.94,
        reflection_need=0.94,
        care_impulse=0.94,
        overstimulation=0.94,
    )

    updated = tick_homeostasis(
        state,
        dt=timedelta(hours=5),
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
    )

    assert updated.curiosity <= 0.95
    assert updated.social_hunger <= 0.95
    assert updated.rest_need <= 0.95
    assert updated.reflection_need <= 0.95
    assert updated.care_impulse <= 0.95
    assert updated.overstimulation <= 0.95


def test_social_phase_biases_reflection_over_curiosity() -> None:
    entered = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    state = HomeostasisState(
        curiosity=0.30,
        social_hunger=0.40,
        rest_need=0.20,
        reflection_need=0.10,
        care_impulse=0.10,
        overstimulation=0.10,
        current_phase=Phase.SOCIAL,
        phase_entered_at=entered,
        last_tick_at=entered,
    )

    updated = tick_homeostasis(
        state,
        dt=timedelta(minutes=10),
        now=entered + timedelta(minutes=10),
    )

    assert updated.reflection_need > state.reflection_need
    assert updated.curiosity > MIN_DRIVE_VALUE
    assert (updated.reflection_need - state.reflection_need) > (
        updated.curiosity - state.curiosity
    ) + HYSTERESIS_MARGIN / 4


def test_resolve_phase_respects_min_dwell_time() -> None:
    now = datetime(2026, 3, 29, 12, 3, tzinfo=UTC)
    state = HomeostasisState(
        curiosity=0.20,
        social_hunger=0.80,
        rest_need=0.10,
        reflection_need=0.10,
        care_impulse=0.10,
        overstimulation=0.10,
        current_phase=Phase.CURIOUS,
        phase_entered_at=now - timedelta(minutes=2),
        last_tick_at=now - timedelta(minutes=2),
    )

    assert resolve_phase(state, now=now) is Phase.CURIOUS


def test_transition_phase_updates_phase_and_timestamp() -> None:
    now = datetime(2026, 3, 29, 20, 0, tzinfo=UTC)
    state = HomeostasisState()

    transitioned = transition_phase(state, Phase.REFLECTIVE, now=now)

    assert transitioned.current_phase is Phase.REFLECTIVE
    assert transitioned.phase_entered_at == now
    assert transitioned.last_tick_at == now


def test_resolve_phase_exits_ambient_when_drives_at_ceiling() -> None:
    """When multiple drives are at ceiling while in AMBIENT, agent must pick one."""
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    state = HomeostasisState(
        curiosity=0.95,
        social_hunger=0.75,
        rest_need=0.10,
        reflection_need=0.95,
        care_impulse=0.95,
        overstimulation=0.10,
        current_phase=Phase.AMBIENT,
        phase_entered_at=now - timedelta(minutes=10),
        last_tick_at=now,
    )

    resolved = resolve_phase(state, now=now)

    assert resolved is not Phase.AMBIENT
    assert resolved in {Phase.CURIOUS, Phase.REFLECTIVE, Phase.CARE}


def test_resolve_phase_stays_in_active_phase_when_drives_tied() -> None:
    """When in an active phase and multiple drives are tied, keep current phase."""
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    state = HomeostasisState(
        curiosity=0.95,
        social_hunger=0.95,
        rest_need=0.10,
        reflection_need=0.95,
        care_impulse=0.95,
        overstimulation=0.10,
        current_phase=Phase.CURIOUS,
        phase_entered_at=now - timedelta(minutes=10),
        last_tick_at=now,
    )

    resolved = resolve_phase(state, now=now)

    assert resolved is Phase.CURIOUS


def test_circadian_modifier_changes_between_day_and_night() -> None:
    day = circadian_modifier(12)
    night = circadian_modifier(2)

    assert day["curiosity"] > night["curiosity"]
    assert night["rest_need"] > day["rest_need"]
