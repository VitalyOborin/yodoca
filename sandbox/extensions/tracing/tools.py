"""Tracing extension tools: history queries and analytics."""

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
    duration_ms: int | None = None
    token_input: int | None = None
    token_output: int | None = None
    cost_usd: float | None = None


class TraceTreeResult(BaseModel):
    success: bool = True
    session_id: str = ""
    spans: list[TraceSpanResult] = Field(default_factory=list)
    total: int = 0
    error: str | None = None


class ToolUsageEntry(BaseModel):
    tool_name: str
    count: int
    avg_duration_ms: float


class SessionCostEntry(BaseModel):
    session_id: str
    cost_usd: float


class ModelCostEntry(BaseModel):
    model: str
    cost_usd: float


class SessionStatsResult(BaseModel):
    success: bool = True
    session_id: str = ""
    turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    top_tools: list[ToolUsageEntry] = Field(default_factory=list)
    error: str | None = None


class ToolUsageResult(BaseModel):
    success: bool = True
    usage: list[ToolUsageEntry] = Field(default_factory=list)
    error: str | None = None


class CostReportResult(BaseModel):
    success: bool = True
    total_cost_usd: float = 0.0
    by_session: list[SessionCostEntry] = Field(default_factory=list)
    by_model: list[ModelCostEntry] = Field(default_factory=list)
    error: str | None = None


class ExplainResult(BaseModel):
    success: bool = True
    explanation: str = ""
    error: str | None = None


def _span_to_result(span: Any) -> TraceSpanResult:
    return TraceSpanResult(
        id=span.id,
        session_id=span.session_id,
        correlation_id=span.correlation_id,
        parent_span_id=span.parent_span_id,
        span_type=span.span_type.value,
        name=span.name,
        input_summary=span.input_summary,
        output_summary=span.output_summary,
        status=span.status.value,
        error_message=span.error_message,
        started_at=span.started_at,
        completed_at=span.completed_at,
        duration_ms=span.duration_ms,
        token_input=span.token_input,
        token_output=span.token_output,
        cost_usd=span.cost_usd,
    )


def _tool_usage_dicts_to_entries(rows: list[dict]) -> list[ToolUsageEntry]:
    return [
        ToolUsageEntry(
            tool_name=str(r["tool_name"]),
            count=int(r["count"]),
            avg_duration_ms=float(r["avg_duration_ms"]),
        )
        for r in rows
    ]


async def _resolve_session_id(storage: TracingStorage, session_id: str) -> str | None:
    """Use explicit session_id or fallback to the latest non-empty session."""
    sid = session_id.strip()
    if sid:
        return sid
    return await storage.get_latest_session_id()


def build_tools(storage: TracingStorage) -> list[Any]:
    """Build tracing tools with access to storage."""

    @function_tool(name_override="tracing_get_last_trace", strict_mode=False)
    async def tracing_get_last_trace(session_id: str) -> TraceTreeResult:
        try:
            spans = await storage.get_last_trace(session_id)
            return TraceTreeResult(
                success=True,
                session_id=session_id,
                spans=[_span_to_result(s) for s in spans],
                total=len(spans),
            )
        except Exception as e:
            return TraceTreeResult(success=False, session_id=session_id, error=str(e))

    @function_tool(name_override="tracing_get_session_stats", strict_mode=False)
    async def tracing_get_session_stats(session_id: str = "") -> SessionStatsResult:
        try:
            resolved_session_id = await _resolve_session_id(storage, session_id)
            if not resolved_session_id:
                return SessionStatsResult(
                    success=True,
                    session_id="",
                    turns=0,
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0.0,
                    top_tools=[],
                )
            stats = await storage.get_session_stats(resolved_session_id)
            top = _tool_usage_dicts_to_entries(list(stats.get("top_tools", [])))
            return SessionStatsResult(
                success=True,
                session_id=resolved_session_id,
                turns=int(stats.get("turns", 0)),
                tokens_in=int(stats.get("tokens_in", 0)),
                tokens_out=int(stats.get("tokens_out", 0)),
                cost_usd=float(stats.get("cost_usd", 0.0)),
                top_tools=top,
            )
        except Exception as e:
            return SessionStatsResult(
                success=False, session_id=session_id.strip(), error=str(e)
            )

    @function_tool(name_override="tracing_get_tool_usage", strict_mode=False)
    async def tracing_get_tool_usage(session_id: str = "") -> ToolUsageResult:
        try:
            resolved_session_id = await _resolve_session_id(storage, session_id)
            usage = await storage.get_tool_usage(session_id=resolved_session_id)
            return ToolUsageResult(
                success=True, usage=_tool_usage_dicts_to_entries(usage)
            )
        except Exception as e:
            return ToolUsageResult(success=False, error=str(e))

    @function_tool(name_override="tracing_get_cost_report", strict_mode=False)
    async def tracing_get_cost_report(session_id: str = "") -> CostReportResult:
        try:
            resolved_session_id = await _resolve_session_id(storage, session_id)
            report = await storage.get_cost_report(session_id=resolved_session_id)
            by_session = [
                SessionCostEntry(
                    session_id=str(r["session_id"]),
                    cost_usd=float(r["cost_usd"]),
                )
                for r in report.get("by_session", [])
            ]
            by_model = [
                ModelCostEntry(model=str(r["model"]), cost_usd=float(r["cost_usd"]))
                for r in report.get("by_model", [])
            ]
            return CostReportResult(
                success=True,
                total_cost_usd=float(report.get("total_cost_usd", 0.0)),
                by_session=by_session,
                by_model=by_model,
            )
        except Exception as e:
            return CostReportResult(success=False, error=str(e))

    @function_tool(name_override="tracing_explain_last_turn", strict_mode=False)
    async def tracing_explain_last_turn(session_id: str) -> ExplainResult:
        try:
            spans = await storage.get_last_trace(session_id)
            if not spans:
                return ExplainResult(
                    success=True,
                    explanation="No trace data found for this session yet.",
                )
            tool_spans = [s for s in spans if s.span_type.value == "tool_call"]
            total_tokens = sum(
                (s.token_input or 0) + (s.token_output or 0) for s in spans
            )
            total_cost = sum(float(s.cost_usd or 0.0) for s in spans)
            if tool_spans:
                calls = ", ".join(s.name for s in tool_spans[:5])
                call_summary = f"called tools: {calls}"
            else:
                call_summary = "no tool calls were detected"
            explanation = (
                f"The agent processed the last turn and {call_summary}. "
                f"Total spans: {len(spans)}, tool calls: {len(tool_spans)}, "
                f"tokens: {total_tokens}, estimated cost: ${total_cost:.6f}."
            )
            return ExplainResult(success=True, explanation=explanation)
        except Exception as e:
            return ExplainResult(success=False, error=str(e))

    return [
        tracing_get_last_trace,
        tracing_get_session_stats,
        tracing_get_tool_usage,
        tracing_get_cost_report,
        tracing_explain_last_turn,
    ]
