"""Tracing extension tools: get_execution_trace, get_trace_stats."""

from __future__ import annotations

from typing import Any

from agents import function_tool
from pydantic import BaseModel, Field

from .storage import TracingStorage


class TraceSpanResult(BaseModel):
    id: str = ""
    session_id: str = ""
    correlation_id: str | None = None
    parent_span_id: str | None = None
    span_type: str = ""
    name: str = ""
    input_summary: str = ""
    output_summary: str = ""
    status: str = ""
    error_message: str | None = None
    started_at: float = 0.0
    completed_at: float | None = None
    duration_ms: float | None = None
    token_input: int | None = None
    token_output: int | None = None


class TraceTreeResult(BaseModel):
    success: bool = True
    session_id: str = ""
    spans: list[TraceSpanResult] = Field(default_factory=list)
    total: int = 0
    error: str | None = None


class TraceStatsResult(BaseModel):
    success: bool = True
    total_spans: int = 0
    completed: int = 0
    errors: int = 0
    running: int = 0
    total_token_input: int = 0
    total_token_output: int = 0
    avg_duration_ms: float = 0.0
    top_tools: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


def build_tools(storage: TracingStorage) -> list[Any]:
    """Build tracing tools with access to storage."""

    @function_tool(name_override="get_execution_trace", strict_mode=False)
    async def get_execution_trace(session_id: str) -> TraceTreeResult:
        """Get the execution trace tree for a session. Returns all spans ordered by time."""
        try:
            spans = await storage.get_trace_tree(session_id)
            return TraceTreeResult(
                success=True,
                session_id=session_id,
                spans=[
                    TraceSpanResult(
                        id=s.id,
                        session_id=s.session_id,
                        correlation_id=s.correlation_id,
                        parent_span_id=s.parent_span_id,
                        span_type=s.span_type.value,
                        name=s.name,
                        input_summary=s.input_summary,
                        output_summary=s.output_summary,
                        status=s.status.value,
                        error_message=s.error_message,
                        started_at=s.started_at,
                        completed_at=s.completed_at,
                        duration_ms=s.duration_ms,
                        token_input=s.token_input,
                        token_output=s.token_output,
                    )
                    for s in spans
                ],
                total=len(spans),
            )
        except Exception as e:
            return TraceTreeResult(success=False, session_id=session_id, error=str(e))

    @function_tool(name_override="get_trace_stats", strict_mode=False)
    async def get_trace_stats(session_id: str = "") -> TraceStatsResult:
        """Get aggregated trace statistics. Optionally filter by session_id."""
        try:
            stats = await storage.get_trace_stats(
                session_id=session_id if session_id else None
            )
            return TraceStatsResult(
                success=True,
                total_spans=stats.get("total_spans", 0),
                completed=stats.get("completed", 0),
                errors=stats.get("errors", 0),
                running=stats.get("running", 0),
                total_token_input=stats.get("total_token_input", 0),
                total_token_output=stats.get("total_token_output", 0),
                avg_duration_ms=stats.get("avg_duration_ms", 0.0),
                top_tools=stats.get("top_tools", []),
            )
        except Exception as e:
            return TraceStatsResult(success=False, error=str(e))

    return [get_execution_trace, get_trace_stats]
