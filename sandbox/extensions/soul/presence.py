"""User presence estimation for Stage 2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

DEFAULT_AVAILABILITY = 0.3


def estimate_availability(
    *,
    now: datetime,
    last_interaction_at: datetime | None,
    slot_interactions: int,
    total_interactions: int,
) -> float:
    """Conservative availability estimate in [0.0, 1.0]."""
    if last_interaction_at is not None:
        delta = now - last_interaction_at
        if delta <= timedelta(minutes=15):
            return 0.95
        if delta <= timedelta(hours=1):
            return 0.8
        if delta <= timedelta(hours=6):
            return 0.55

    if total_interactions < 3:
        return DEFAULT_AVAILABILITY

    slot_ratio = min(slot_interactions / max(total_interactions, 1), 1.0)
    score = DEFAULT_AVAILABILITY + (slot_ratio * 0.6)
    return max(0.0, min(0.85, score))


def normalize_presence_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)
