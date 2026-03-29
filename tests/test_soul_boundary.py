from datetime import UTC, datetime

from sandbox.extensions.soul.boundary import BoundaryDecision, check_outreach
from sandbox.extensions.soul.models import CompanionState, OutreachResult, Phase


def test_boundary_blocks_at_night() -> None:
    state = CompanionState()
    state.user_presence.estimated_availability = 0.9

    outcome = check_outreach(state, now=datetime(2026, 3, 29, 23, 0, tzinfo=UTC))

    assert outcome.decision is BoundaryDecision.BLOCK
    assert outcome.reason == "night_window"


def test_boundary_uses_local_hour_override_for_night_window() -> None:
    state = CompanionState()
    state.user_presence.estimated_availability = 0.9

    outcome = check_outreach(
        state,
        now=datetime(2026, 3, 29, 20, 0, tzinfo=UTC),
        local_hour=23,
    )

    assert outcome.decision is BoundaryDecision.BLOCK
    assert outcome.reason == "night_window"


def test_boundary_blocks_in_resting_phase() -> None:
    state = CompanionState()
    state.homeostasis.current_phase = Phase.RESTING
    state.user_presence.estimated_availability = 0.9

    outcome = check_outreach(state, now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC))

    assert outcome.decision is BoundaryDecision.BLOCK
    assert outcome.reason == "resting_phase"


def test_boundary_blocks_on_active_cooldown() -> None:
    state = CompanionState()
    state.user_presence.estimated_availability = 0.9
    state.initiative.cooldown_until = datetime(2026, 3, 29, 18, 0, tzinfo=UTC)
    state.initiative.last_outreach_result = OutreachResult.IGNORED

    outcome = check_outreach(state, now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC))

    assert outcome.decision is BoundaryDecision.BLOCK
    assert outcome.reason == "cooldown_active"


def test_boundary_defers_when_availability_is_uncertain() -> None:
    state = CompanionState()
    state.user_presence.estimated_availability = 0.4

    outcome = check_outreach(state, now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC))

    assert outcome.decision is BoundaryDecision.DEFER
    assert outcome.reason == "uncertain_availability"


def test_boundary_allows_natural_window() -> None:
    state = CompanionState()
    state.user_presence.estimated_availability = 0.8
    state.initiative.cooldown_until = datetime(2026, 3, 29, 10, 0, tzinfo=UTC)
    state.homeostasis.current_phase = Phase.AMBIENT
    state.initiative.budget.used_today = 0

    outcome = check_outreach(state, now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC))

    assert outcome.decision is BoundaryDecision.ALLOW
    assert outcome.reason == "natural_window"
