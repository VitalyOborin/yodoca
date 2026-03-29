"""Initiative domain state machine for Stage 2."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sandbox.extensions.soul.models import (
    InitiativeBudget,
    InitiativeState,
    OutreachResult,
    PendingOutreach,
)

IGNORED_COOLDOWN = timedelta(hours=6)
IGNORED_COOLDOWN_DISCOVERY = timedelta(hours=2)
REJECTED_COOLDOWN = timedelta(days=2)
RESPONSE_COOLDOWN = timedelta(minutes=15)
OUTREACH_WINDOW = timedelta(minutes=60)


def refresh_budget(
    budget: InitiativeBudget,
    *,
    now: datetime | None = None,
) -> InitiativeBudget:
    now = now or datetime.now(UTC)
    if budget.last_reset_at.date() == now.date():
        return budget
    return InitiativeBudget(
        daily_budget=budget.daily_budget,
        used_today=0,
        last_reset_at=now,
    )


def can_attempt_outreach(
    state: InitiativeState,
    *,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(UTC)
    budget = refresh_budget(state.budget, now=now)
    if budget.used_today >= budget.daily_budget:
        return False
    if state.pending_outreach is not None:
        return False
    if state.cooldown_until is not None and state.cooldown_until > now:
        return False
    return True


def register_outreach_attempt(
    state: InitiativeState,
    *,
    outreach_id: str,
    channel_id: str | None,
    availability_at_send: float,
    now: datetime | None = None,
) -> InitiativeState:
    now = now or datetime.now(UTC)
    budget = refresh_budget(state.budget, now=now)
    if budget.used_today >= budget.daily_budget:
        raise ValueError("Daily initiative budget exhausted")
    if state.pending_outreach is not None:
        raise ValueError("Pending outreach already exists")

    pending = PendingOutreach(
        outreach_id=outreach_id,
        channel_id=channel_id,
        attempted_at=now,
        availability_at_send=availability_at_send,
        window_deadline_at=now + OUTREACH_WINDOW,
    )
    return InitiativeState(
        budget=replace(budget, used_today=budget.used_today + 1),
        pending_outreach=pending,
        cooldown_until=state.cooldown_until,
        adaptive_threshold=state.adaptive_threshold,
        last_outreach_at=now,
        last_outreach_result=state.last_outreach_result,
        last_result_at=state.last_result_at,
    )


def resolve_outreach(
    state: InitiativeState,
    *,
    result: OutreachResult,
    now: datetime | None = None,
    apply_cooldown: bool = True,
    discovery_mode: bool = False,
) -> InitiativeState:
    now = now or datetime.now(UTC)
    if state.pending_outreach is None:
        raise ValueError("No pending outreach to resolve")

    cooldown_until = state.cooldown_until
    if apply_cooldown:
        if result is OutreachResult.IGNORED:
            cooldown_until = now + (
                IGNORED_COOLDOWN_DISCOVERY if discovery_mode else IGNORED_COOLDOWN
            )
        elif result is OutreachResult.REJECTED:
            cooldown_until = now + REJECTED_COOLDOWN
        elif result is OutreachResult.RESPONSE:
            cooldown_until = now + RESPONSE_COOLDOWN
        elif result is OutreachResult.TIMING_MISS:
            cooldown_until = None

    return InitiativeState(
        budget=refresh_budget(state.budget, now=now),
        pending_outreach=None,
        cooldown_until=cooldown_until,
        adaptive_threshold=state.adaptive_threshold,
        last_outreach_at=state.last_outreach_at,
        last_outreach_result=result,
        last_result_at=now,
    )
