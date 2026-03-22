"""AgentInvoker: invoke and stream agent calls with lock separation."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast, runtime_checkable

from core.extensions.contract import TurnContext
from core.extensions.persistence.thread_manager import ThreadManager
from core.extensions.routing.approval_coordinator import ApprovalCoordinator

logger = logging.getLogger(__name__)


@runtime_checkable
class TraceHook(Protocol):
    """Optional hook for execution tracing. No-op when no hook is registered."""

    async def on_invoke_start(self, prompt: str, session_id: str, agent_id: str) -> str:
        """Called before agent invocation. Returns span_id."""
        ...

    async def on_invoke_end(
        self, span_id: str, output: str, error: str | None = None
    ) -> None:
        """Called after agent invocation completes or fails."""
        ...

    async def on_tool_call(
        self, parent_span_id: str, tool_name: str, arguments: str
    ) -> str:
        """Called when a tool call is detected. Returns span_id."""
        ...

    async def on_tool_result(
        self, span_id: str, result: str, error: str | None = None
    ) -> None:
        """Called when a tool call completes."""
        ...


def _get_response_delta_type() -> type | None:
    try:
        from openai.types.responses import ResponseTextDeltaEvent

        return ResponseTextDeltaEvent
    except Exception:
        return None


class AgentInvoker:
    """Encapsulates agent execution, middleware context injection, and locks."""

    def __init__(
        self,
        approval_coordinator: ApprovalCoordinator,
        thread_manager: ThreadManager,
    ) -> None:
        self._approval = approval_coordinator
        self._threads = thread_manager
        self._agent: Any = None
        self._agent_id: str = "orchestrator"
        self._user_lock = asyncio.Lock()
        self._background_lock = asyncio.Lock()
        self._invoke_middleware: Callable[[str, TurnContext], Awaitable[str]] | None = (
            None
        )
        self._trace_hooks: list[TraceHook] = []

    def register_trace_hook(self, hook: TraceHook) -> None:
        """Register a tracing hook. Multiple hooks are supported."""
        self._trace_hooks.append(hook)

    async def _fire_invoke_start(
        self, prompt: str, session_id: str, agent_id: str
    ) -> list[str]:
        span_ids: list[str] = []
        for hook in self._trace_hooks:
            try:
                sid = await hook.on_invoke_start(prompt, session_id, agent_id)
                span_ids.append(sid)
            except Exception:
                logger.debug("TraceHook.on_invoke_start failed", exc_info=True)
                span_ids.append("")
        return span_ids

    async def _fire_invoke_end(
        self, span_ids: list[str], output: str, error: str | None = None
    ) -> None:
        for hook, sid in zip(self._trace_hooks, span_ids, strict=False):
            if not sid:
                continue
            try:
                await hook.on_invoke_end(sid, output, error)
            except Exception:
                logger.debug("TraceHook.on_invoke_end failed", exc_info=True)

    async def _fire_tool_call(
        self, span_ids: list[str], tool_name: str, arguments: str
    ) -> list[str]:
        tool_span_ids: list[str] = []
        for hook, parent_sid in zip(self._trace_hooks, span_ids, strict=False):
            if not parent_sid:
                tool_span_ids.append("")
                continue
            try:
                tsid = await hook.on_tool_call(parent_sid, tool_name, arguments)
                tool_span_ids.append(tsid)
            except Exception:
                logger.debug("TraceHook.on_tool_call failed", exc_info=True)
                tool_span_ids.append("")
        return tool_span_ids

    async def _fire_tool_result(
        self,
        tool_span_ids: list[str],
        result: str,
        error: str | None = None,
    ) -> None:
        for hook, span_id in zip(self._trace_hooks, tool_span_ids, strict=False):
            if not span_id:
                continue
            try:
                await hook.on_tool_result(span_id, result, error)
            except Exception:
                logger.debug("TraceHook.on_tool_result failed", exc_info=True)

    def set_agent(self, agent: Any, agent_id: str = "orchestrator") -> None:
        self._agent = agent
        self._agent_id = agent_id

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def middleware(self) -> Callable[[str, TurnContext], Awaitable[str]] | None:
        return self._invoke_middleware

    @middleware.setter
    def middleware(
        self,
        value: Callable[[str, TurnContext], Awaitable[str]] | None,
    ) -> None:
        self._invoke_middleware = value

    async def _get_context_for_prompt(
        self, prompt: str, turn_context: TurnContext | None
    ) -> tuple[str, TurnContext, str]:
        stripped = prompt.strip()
        ctx = turn_context or TurnContext(agent_id=self._agent_id)
        context = ""
        if self._invoke_middleware:
            context = await self._invoke_middleware(stripped, ctx) or ""
        return stripped, ctx, context

    async def _prepare_agent(
        self,
        prompt: str,
        turn_context: TurnContext | None = None,
    ) -> tuple[Any, str]:
        stripped, _ctx, context = await self._get_context_for_prompt(
            prompt, turn_context
        )
        agent = self._agent
        if context and isinstance(getattr(self._agent, "instructions", None), str):
            agent = self._agent.clone(
                instructions=self._agent.instructions + "\n\n---\n\n" + context
            )
        return agent, stripped

    async def enrich_prompt(
        self,
        prompt: str,
        turn_context: TurnContext | None = None,
    ) -> str:
        stripped, _ctx, context = await self._get_context_for_prompt(
            prompt, turn_context
        )
        if not context:
            return stripped
        return context + "\n\n---\n\n" + stripped

    async def invoke_agent(
        self,
        prompt: str,
        turn_context: TurnContext | None = None,
        thread: Any = None,
        session: Any = None,
    ) -> str:
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        sess = session if session is not None else thread
        if sess is None:
            sess = self._threads.thread
        session_id = str(getattr(sess, "id", "unknown"))
        span_ids = await self._fire_invoke_start(stripped, session_id, self._agent_id)
        async with self._user_lock:
            try:
                channel_id = turn_context.channel_id if turn_context else None
                result = await self._approval.run_with_approval_loop(
                    agent=agent,
                    input_or_state=stripped,
                    session=sess,
                    channel_id=channel_id,
                )
                output = result.final_output or ""
                await self._fire_invoke_end(span_ids, output)
                return output
            except Exception as e:
                logger.exception("Agent invocation failed: %s", e)
                await self._fire_invoke_end(span_ids, "", error=str(e))
                return f"(Error: {e})"

    async def _consume_stream_events(
        self,
        result: Any,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None,
        parent_span_ids: list[str] | None = None,
    ) -> tuple[str, BaseException | None, int | None, int, int, list[list[str]]]:
        full_text = ""
        first_delta_ms: int | None = None
        delta_count = 0
        tool_call_count = 0
        collected_tool_span_ids: list[list[str]] = []
        started_at = time.perf_counter()
        try:
            response_delta_type = _get_response_delta_type()
            async for event in result.stream_events():
                delta = self._extract_stream_delta(event, response_delta_type)
                if delta is not None:
                    delta_count += 1
                    if first_delta_ms is None:
                        first_delta_ms = int((time.perf_counter() - started_at) * 1000)
                    full_text += delta
                    await on_chunk(delta)
                elif event.type == "run_item_stream_event":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) == "tool_call_item":
                        tool_call_count += 1
                        raw_item = getattr(item, "raw_item", None)
                        tool_name = str(getattr(raw_item, "name", "tool"))
                        if on_tool_call:
                            await on_tool_call(tool_name)
                        if parent_span_ids and self._trace_hooks:
                            ids = await self._fire_tool_call(
                                parent_span_ids, tool_name, ""
                            )
                            collected_tool_span_ids.append(ids)
            return (
                full_text,
                None,
                first_delta_ms,
                delta_count,
                tool_call_count,
                collected_tool_span_ids,
            )
        except BaseException as e:
            return (
                full_text,
                e,
                first_delta_ms,
                delta_count,
                tool_call_count,
                collected_tool_span_ids,
            )

    def _extract_stream_delta(
        self,
        event: Any,
        response_delta_type: type | None,
    ) -> str | None:
        if event.type != "raw_response_event":
            return None
        event_data = getattr(event, "data", None)
        if response_delta_type is not None and isinstance(
            event_data, response_delta_type
        ):
            return getattr(event_data, "delta", None)
        if event_data is not None and hasattr(event_data, "delta"):
            return cast(str | None, event_data.delta)
        return None

    async def _run_streamed_invoke(
        self,
        agent: Any,
        stripped: str,
        thread: Any,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None,
        use_background_lock: bool,
    ) -> str:
        session_id = str(getattr(thread, "id", "unknown"))
        span_ids = await self._fire_invoke_start(stripped, session_id, self._agent_id)
        lock = self._background_lock if use_background_lock else self._user_lock
        async with lock:
            full_text = ""
            started_at = time.perf_counter()
            try:
                from agents import Runner

                result = Runner.run_streamed(agent, stripped, session=thread)
                (
                    full_text,
                    stream_error,
                    first_delta_ms,
                    delta_count,
                    tool_call_count,
                    tool_span_batches,
                ) = await self._consume_stream_events(
                    result=result,
                    on_chunk=on_chunk,
                    on_tool_call=on_tool_call,
                    parent_span_ids=span_ids,
                )
                if stream_error is not None:
                    raise stream_error
                output = result.final_output or full_text
                for tool_span_ids in tool_span_batches:
                    await self._fire_tool_result(tool_span_ids, "")
                await self._fire_invoke_end(span_ids, output)
                logger.info(
                    "Agent stream completed: first_delta_ms=%s duration_ms=%d "
                    "delta_count=%d tool_call_count=%d prompt_len=%d",
                    first_delta_ms,
                    int((time.perf_counter() - started_at) * 1000),
                    delta_count,
                    tool_call_count,
                    len(stripped),
                )
                return output
            except Exception as e:
                logger.exception("Agent streaming invocation failed: %s", e)
                for tool_span_ids in locals().get("tool_span_batches", []):
                    await self._fire_tool_result(tool_span_ids, "", error=str(e))
                await self._fire_invoke_end(span_ids, full_text, error=str(e))
                if full_text:
                    try:
                        await on_chunk(f"\n(Error: {e})")
                    except Exception:
                        logger.exception(
                            "Error callback failed while reporting stream error"
                        )
                    return full_text + f"\n(Error: {e})"
                return f"(Error: {e})"

    async def invoke_agent_streamed(
        self,
        prompt: str,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None = None,
        turn_context: TurnContext | None = None,
        thread: Any = None,
        session: Any = None,
    ) -> str:
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        sess = session if session is not None else thread
        if sess is None:
            sess = self._threads.thread
        return await self._run_streamed_invoke(
            agent=agent,
            stripped=stripped,
            thread=sess,
            on_chunk=on_chunk,
            on_tool_call=on_tool_call,
            use_background_lock=False,
        )

    async def invoke_agent_background(
        self,
        prompt: str,
        turn_context: TurnContext | None = None,
    ) -> str:
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        session = self._threads.get_background_thread()
        session_id = str(getattr(session, "id", "unknown"))
        span_ids = await self._fire_invoke_start(stripped, session_id, self._agent_id)
        async with self._background_lock:
            try:
                channel_id = turn_context.channel_id if turn_context else None
                result = await self._approval.run_with_approval_loop(
                    agent=agent,
                    input_or_state=stripped,
                    session=session,
                    channel_id=channel_id,
                )
                output = result.final_output or ""
                await self._fire_invoke_end(span_ids, output)
                return output
            except Exception as e:
                logger.exception("Agent background invocation failed: %s", e)
                await self._fire_invoke_end(span_ids, "", error=str(e))
                return f"(Error: {e})"

    async def invoke_agent_background_streamed(
        self,
        prompt: str,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None = None,
        turn_context: TurnContext | None = None,
    ) -> str:
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        session = self._threads.get_background_thread()
        return await self._run_streamed_invoke(
            agent=agent,
            stripped=stripped,
            thread=session,
            on_chunk=on_chunk,
            on_tool_call=on_tool_call,
            use_background_lock=True,
        )
