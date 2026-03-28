from datetime import UTC, datetime, timedelta

from sandbox.extensions.soul.initiative import (
    IGNORED_COOLDOWN,
    can_attempt_outreach,
    refresh_budget,
    register_outreach_attempt,
    resolve_outreach,
)
from sandbox.extensions.soul.models import (
    InitiativeBudget,
    InitiativeState,
    OutreachResult,
)


def test_budget_refresh_resets_on_new_day() -> None:
    budget = InitiativeBudget(
        daily_budget=1,
        used_today=1,
        last_reset_at=datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
    )

    refreshed = refresh_budget(budget, now=datetime(2026, 3, 29, 8, 0, tzinfo=UTC))

    assert refreshed.used_today == 0


def test_register_outreach_consumes_budget_and_creates_pending_window() -> None:
    state = InitiativeState()
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)

    updated = register_outreach_attempt(
        state,
        outreach_id="outreach-1",
        channel_id="cli_channel",
        availability_at_send=0.8,
        now=now,
    )

    assert updated.budget.used_today == 1
    assert updated.pending_outreach is not None
    assert updated.pending_outreach.outreach_id == "outreach-1"
    assert updated.pending_outreach.window_deadline_at == now + timedelta(minutes=60)


def test_resolve_ignored_applies_cooldown() -> None:
    state = register_outreach_attempt(
        InitiativeState(),
        outreach_id="outreach-1",
        channel_id="cli_channel",
        availability_at_send=0.8,
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
    )
    resolved_at = datetime(2026, 3, 29, 13, 0, tzinfo=UTC)

    resolved = resolve_outreach(
        state,
        result=OutreachResult.IGNORED,
        now=resolved_at,
    )

    assert resolved.pending_outreach is None
    assert resolved.last_outreach_result is OutreachResult.IGNORED
    assert resolved.cooldown_until == resolved_at + IGNORED_COOLDOWN
    assert can_attempt_outreach(resolved, now=resolved_at) is False


def test_response_clears_pending_without_cooldown() -> None:
    state = register_outreach_attempt(
        InitiativeState(),
        outreach_id="outreach-1",
        channel_id="cli_channel",
        availability_at_send=0.8,
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
    )
    resolved = resolve_outreach(
        state,
        result=OutreachResult.RESPONSE,
        now=datetime(2026, 3, 29, 12, 10, tzinfo=UTC),
    )

    assert resolved.pending_outreach is None
    assert resolved.cooldown_until is None
