"""Execution Tracing extension: records agent invocations as nested spans."""

import logging
import time
from typing import Any

from .models import Span, SpanStatus, SpanType
from .storage import TracingStorage
from .tools import build_tools

logger = logging.getLogger(__name__)


class TracingExtension:
    """Extension + ToolProvider + SchedulerProvider: structured execution tracing."""

    def __init__(self) -> None:
        self._context: Any = None
        self._storage: TracingStorage | None = None
        self._max_input_len: int = 2000
        self._max_output_len: int = 2000
        self._trace_tool_calls: bool = True

    async def initialize(self, context: Any) -> None:
        self._context = context
        self._max_input_len = context.get_config("max_input_summary_len", 2000)
        self._max_output_len = context.get_config("max_output_summary_len", 2000)
        self._trace_tool_calls = context.get_config("trace_tool_calls", True)

        db_path = context.data_dir / "traces.db"
        self._storage = TracingStorage(db_path)
        await self._storage.initialize()

        # Register as a TraceHook on the AgentInvoker
        try:
            invoker = context._router._invoker
            if hasattr(invoker, "register_trace_hook"):
                invoker.register_trace_hook(self)
                logger.info("Tracing hook registered on AgentInvoker")
        except Exception:
            logger.warning("Could not register trace hook on AgentInvoker", exc_info=True)

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
            retention = 30
            if self._context:
                retention = self._context.get_config("retention_days", 30)
            deleted = await self._storage.cleanup_old_traces(retention)
            logger.info("Cleaned up %d old trace spans", deleted)
            if deleted > 0:
                return {"text": f"Tracing cleanup: deleted {deleted} old spans."}
        return None

    # -- TraceHook protocol methods --

    async def on_invoke_start(
        self, prompt: str, session_id: str, agent_id: str
    ) -> str:
        span = Span(
            session_id=session_id,
            span_type=SpanType.AGENT_INVOKE,
            name=agent_id,
            input_summary=prompt[: self._max_input_len],
            status=SpanStatus.RUNNING,
            started_at=time.time(),
        )
        if self._storage:
            await self._storage.save_span(span)
        if self._context:
            try:
                await self._context.emit(
                    "trace.span.started",
                    {
                        "span_id": span.id,
                        "session_id": session_id,
                        "span_type": "agent_invoke",
                        "name": agent_id,
                    },
                )
            except Exception:
                logger.debug("Failed to emit trace.span.started", exc_info=True)
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
        if error:
            span.status = SpanStatus.ERROR
            span.error_message = error
        else:
            span.status = SpanStatus.COMPLETED
        await self._storage.update_span(span)
        if self._context:
            try:
                await self._context.emit(
                    "trace.span.completed",
                    {
                        "span_id": span_id,
                        "session_id": span.session_id,
                        "span_type": span.span_type.value,
                        "status": span.status.value,
                        "duration_ms": span.duration_ms,
                    },
                )
            except Exception:
                logger.debug("Failed to emit trace.span.completed", exc_info=True)

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
        )
        await self._storage.save_span(span)
        if self._context:
            try:
                await self._context.emit(
                    "trace.span.started",
                    {
                        "span_id": span.id,
                        "session_id": session_id,
                        "span_type": "tool_call",
                        "name": tool_name,
                        "parent_span_id": parent_span_id,
                    },
                )
            except Exception:
                logger.debug("Failed to emit trace.span.started for tool", exc_info=True)
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
        if error:
            span.status = SpanStatus.ERROR
            span.error_message = error
        else:
            span.status = SpanStatus.COMPLETED
        await self._storage.update_span(span)
        if self._context:
            try:
                await self._context.emit(
                    "trace.span.completed",
                    {
                        "span_id": span_id,
                        "session_id": span.session_id,
                        "span_type": "tool_call",
                        "status": span.status.value,
                        "duration_ms": span.duration_ms,
                    },
                )
            except Exception:
                logger.debug("Failed to emit trace.span.completed for tool", exc_info=True)
