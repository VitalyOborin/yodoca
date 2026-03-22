"""Execution Tracing extension: records agent invocations as nested spans."""

import logging
import time
from typing import Any

from .models import Span, SpanStatus, SpanType
from .storage import TracingStorage
from .token_counter import TokenCounter
from .tools import build_tools

logger = logging.getLogger(__name__)


class TracingExtension:
    """Extension + ToolProvider + SchedulerProvider: structured execution tracing."""

    def __init__(self) -> None:
        self._context: Any = None
        self._storage: TracingStorage | None = None
        self._token_counter = TokenCounter()
        self._max_input_len: int = 2000
        self._max_output_len: int = 2000
        self._trace_tool_calls: bool = True
        self._warn_at_session_tokens: int = 0
        self._stop_at_session_tokens: int = 0
        self._session_tokens_total: dict[str, int] = {}
        self._budget_warned_sessions: set[str] = set()
        self._budget_exceeded_sessions: set[str] = set()

    async def initialize(self, context: Any) -> None:
        self._context = context
        self._max_input_len = context.get_config("max_input_summary_len", 2000)
        self._max_output_len = context.get_config("max_output_summary_len", 2000)
        self._trace_tool_calls = context.get_config("trace_tool_calls", True)
        self._warn_at_session_tokens = int(
            context.get_config("warn_at_session_tokens", 0)
        )
        self._stop_at_session_tokens = int(
            context.get_config("stop_at_session_tokens", 0)
        )
        pricing = context.get_config("pricing", {})
        if isinstance(pricing, dict):
            self._token_counter.update_pricing(pricing)

        db_path = context.data_dir / "traces.db"
        self._storage = TracingStorage(db_path)
        await self._storage.initialize()

        # Register as a TraceHook on the AgentInvoker.
        try:
            invoker = context._router._invoker
            if hasattr(invoker, "register_trace_hook"):
                invoker.register_trace_hook(self)
                logger.info("Tracing hook registered on AgentInvoker")
        except Exception:
            logger.warning(
                "Could not register trace hook on AgentInvoker", exc_info=True
            )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        if self._storage:
            await self._storage.close()
            self._storage = None

    def health_check(self) -> bool:
        return self._storage is not None

    # -- ToolProvider --

    def get_tools(self) -> list[Any]:
        if not self._storage:
            return []
        return build_tools(self._storage)

    # -- SchedulerProvider --

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        if task_name == "cleanup_old_traces" and self._storage:
            retention = (
                self._context.get_config("retention_days", 30) if self._context else 30
            )
            deleted = await self._storage.cleanup_old_traces(int(retention))
            do_vacuum = bool(self._context.get_config("vacuum_after_cleanup", True))
            if do_vacuum:
                await self._storage.vacuum()
            logger.info("Cleaned up %d old trace spans (vacuum=%s)", deleted, do_vacuum)
            return {
                "text": (
                    f"Tracing cleanup: deleted {deleted} old spans"
                    f"{' and vacuumed DB' if do_vacuum else ''}."
                )
            }
        return None

    async def _emit_span_update(self, phase: str, span: Span) -> None:
        if not self._context:
            return
        payload = {
            "phase": phase,
            "span_id": span.id,
            "session_id": span.session_id,
            "span_type": span.span_type.value,
            "name": span.name,
            "status": span.status.value,
            "parent_span_id": span.parent_span_id,
            "duration_ms": span.duration_ms,
            "token_input": span.token_input,
            "token_output": span.token_output,
            "cost_usd": span.cost_usd,
        }
        try:
            await self._context.emit("tracing.span.update", payload)
            # Backward compatibility for existing integrations in this branch.
            topic = (
                "trace.span.started" if phase == "started" else "trace.span.completed"
            )
            await self._context.emit(topic, payload)
        except Exception:
            logger.debug("Failed to emit tracing span update", exc_info=True)

    async def _emit_budget_event(
        self,
        topic: str,
        session_id: str,
        total_tokens: int,
        threshold: int,
    ) -> None:
        if not self._context:
            return
        try:
            await self._context.emit(
                topic,
                {
                    "session_id": session_id,
                    "session_tokens_total": total_tokens,
                    "threshold": threshold,
                },
            )
        except Exception:
            logger.debug("Failed to emit budget event %s", topic, exc_info=True)

    async def _check_budget(self, session_id: str, total_tokens: int) -> None:
        if (
            self._warn_at_session_tokens > 0
            and total_tokens >= self._warn_at_session_tokens
            and session_id not in self._budget_warned_sessions
        ):
            self._budget_warned_sessions.add(session_id)
            await self._emit_budget_event(
                "tracing.budget.warning",
                session_id,
                total_tokens,
                self._warn_at_session_tokens,
            )
        if (
            self._stop_at_session_tokens > 0
            and total_tokens >= self._stop_at_session_tokens
            and session_id not in self._budget_exceeded_sessions
        ):
            self._budget_exceeded_sessions.add(session_id)
            await self._emit_budget_event(
                "tracing.budget.exceeded",
                session_id,
                total_tokens,
                self._stop_at_session_tokens,
            )

    # -- TraceHook protocol methods --

    async def on_invoke_start(self, prompt: str, session_id: str, agent_id: str) -> str:
        span = Span(
            session_id=session_id,
            span_type=SpanType.AGENT_INVOKE,
            name=agent_id,
            input_summary=prompt[: self._max_input_len],
            status=SpanStatus.RUNNING,
            started_at=time.time(),
        )
        span.token_input = self._token_counter.count_tokens(prompt)
        if self._storage:
            await self._storage.save_span(span)
        await self._emit_span_update("started", span)
        return span.id

    async def on_invoke_end(
        self, span_id: str, output: str, error: str | None = None
    ) -> None:
        if not self._storage:
            return
        span = await self._storage.get_span(span_id)
        if not span:
            return
        now = time.time()
        span.completed_at = now
        span.duration_ms = round((now - span.started_at) * 1000, 2)
        span.output_summary = output[: self._max_output_len]
        span.token_output = self._token_counter.count_tokens(output)
        model = str(span.metadata.get("model", "")) if span.metadata else ""
        span.cost_usd = self._token_counter.calculate_cost(
            span.token_input or 0,
            span.token_output or 0,
            model=model or None,
        )
        if error:
            span.status = SpanStatus.ERROR
            span.error_message = error
        else:
            span.status = SpanStatus.COMPLETED
        await self._storage.update_span(span)

        session_total = self._session_tokens_total.get(span.session_id, 0)
        session_total += (span.token_input or 0) + (span.token_output or 0)
        self._session_tokens_total[span.session_id] = session_total
        await self._check_budget(span.session_id, session_total)
        await self._emit_span_update("completed", span)

    async def on_tool_call(
        self, parent_span_id: str, tool_name: str, arguments: str
    ) -> str:
        if not self._trace_tool_calls or not self._storage:
            return ""
        parent = await self._storage.get_span(parent_span_id)
        session_id = parent.session_id if parent else ""
        span = Span(
            session_id=session_id,
            parent_span_id=parent_span_id,
            span_type=SpanType.TOOL_CALL,
            name=tool_name,
            input_summary=arguments[: self._max_input_len],
            status=SpanStatus.RUNNING,
            started_at=time.time(),
            token_input=self._token_counter.approximate_tokens(arguments),
        )
        await self._storage.save_span(span)
        await self._emit_span_update("started", span)
        return span.id

    async def on_tool_result(
        self, span_id: str, result: str, error: str | None = None
    ) -> None:
        if not self._storage:
            return
        span = await self._storage.get_span(span_id)
        if not span:
            return
        now = time.time()
        span.completed_at = now
        span.duration_ms = round((now - span.started_at) * 1000, 2)
        span.output_summary = result[: self._max_output_len]
        span.token_output = self._token_counter.approximate_tokens(result)
        if error:
            span.status = SpanStatus.ERROR
            span.error_message = error
        else:
            span.status = SpanStatus.COMPLETED
        await self._storage.update_span(span)
        await self._emit_span_update("completed", span)
