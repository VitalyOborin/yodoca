"""Tool models for the soul runtime."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SoulStateResult(BaseModel):
    """Structured snapshot returned by get_soul_state."""

    success: bool
    status: str = "ok"
    health: bool
    phase: str
    presence: str
    mood: float
    tick_count: int
    uptime_seconds: int
    time_in_phase_seconds: int
    last_tick_at: str | None = None
    drives: dict[str, float] = Field(default_factory=dict)
    perception: dict[str, float] = Field(default_factory=dict)
    initiative: dict[str, str | int | float | None] = Field(default_factory=dict)
    user_presence: dict[str, str | int | float | None] = Field(default_factory=dict)
    error: str | None = None
