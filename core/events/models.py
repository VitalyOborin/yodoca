"""Event model for the Event Bus."""

from dataclasses import dataclass

__all__ = ["Event"]


@dataclass(frozen=True)
class Event:
    """Immutable event passed to handlers."""

    id: int
    topic: str
    source: str
    payload: dict
    created_at: float
    correlation_id: str | None = None
    status: str = "pending"
    retry_count: int = 0
