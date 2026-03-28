"""Boundary governor for controlled initiative."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sandbox.extensions.soul.models import CompanionState, Phase


class BoundaryDecision(StrEnum):
    ALLOW = "allow"
    DEFER = "defer"
    BLOCK = "block"


@dataclass(slots=True)
class BoundaryOutcome:
    decision: BoundaryDecision
    reason: str


def check_outreach(
    state: CompanionState,
    *,
    now: datetime | None = None,
) -> BoundaryOutcome:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    initiative = state.initiative
    budget = initiative.budget
    availability = state.user_presence.estimated_availability

    if initiative.pending_outreach is not None:
        return BoundaryOutcome(BoundaryDecision.BLOCK, "pending_outreach")
    if budget.used_today >= budget.daily_budget:
        return BoundaryOutcome(BoundaryDecision.BLOCK, "daily_budget_exhausted")
    if initiative.cooldown_until is not None and initiative.cooldown_until > now:
        return BoundaryOutcome(BoundaryDecision.BLOCK, "cooldown_active")
    if state.homeostasis.current_phase is Phase.RESTING:
        return BoundaryOutcome(BoundaryDecision.BLOCK, "resting_phase")
    if now.hour >= 22 or now.hour < 7:
        return BoundaryOutcome(BoundaryDecision.BLOCK, "night_window")
    if availability < 0.3:
        return BoundaryOutcome(BoundaryDecision.BLOCK, "low_availability")
    if availability < 0.5:
        return BoundaryOutcome(BoundaryDecision.DEFER, "uncertain_availability")
    return BoundaryOutcome(BoundaryDecision.ALLOW, "natural_window")
