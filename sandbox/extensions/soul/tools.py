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
    temperament: dict[str, str | int | float | None] = Field(default_factory=dict)
    discovery: dict[str, object] = Field(default_factory=dict)
    recovery: dict[str, object] = Field(default_factory=dict)
    error: str | None = None


class SoulMetricsResult(BaseModel):
    """Structured snapshot returned by get_soul_metrics."""

    success: bool
    status: str = "ok"
    current_context_words: int
    context_words_avg_7d: float
    outreach_quality_7d: dict[str, int | float] = Field(default_factory=dict)
    perception_corrections_7d: int
    openness_trend: float
    message_depth_trend: float
    initiative_ratio_trend: float
    alerts: list[str] = Field(default_factory=list)
