"""Execution tracing data models: Span, SpanType, SpanStatus."""

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SpanType(StrEnum):
    AGENT_INVOKE = "agent_invoke"
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    CONTEXT_ENRICHMENT = "context_enrichment"
    MEMORY_RETRIEVAL = "memory_retrieval"


class SpanStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class Span:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    correlation_id: str | None = None
    parent_span_id: str | None = None
    span_type: SpanType = SpanType.AGENT_INVOKE
    name: str = ""
    input_summary: str = ""
    output_summary: str = ""
    status: SpanStatus = SpanStatus.RUNNING
    error_message: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    duration_ms: int | None = None
    token_input: int | None = None
    token_output: int | None = None
    cost_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpanUpdatePayload:
    """Structured payload for tracing.span.update (and legacy trace.span.* topics)."""

    phase: str
    span_id: str
    session_id: str
    span_type: str
    name: str
    status: str
    parent_span_id: str | None
    duration_ms: int | None
    token_input: int | None
    token_output: int | None
    cost_usd: float | None


@dataclass
class BudgetEventPayload:
    """Structured payload for tracing.budget.warning / tracing.budget.exceeded."""

    session_id: str
    session_tokens_total: int
    threshold: int
