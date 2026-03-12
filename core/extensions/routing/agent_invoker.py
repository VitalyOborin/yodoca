"""AgentInvoker: invoke and stream agent calls with lock separation."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from core.extensions.contract import TurnContext
from core.extensions.persistence.thread_manager import ThreadManager
from core.extensions.routing.approval_coordinator import ApprovalCoordinator

logger = logging.getLogger(__name__)


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
        async with self._user_lock:
            try:
                channel_id = turn_context.channel_id if turn_context else None
                result = await self._approval.run_with_approval_loop(
                    agent=agent,
                    input_or_state=stripped,
                    session=sess,
                    channel_id=channel_id,
                )
                return result.final_output or ""
            except Exception as e:
                logger.exception("Agent invocation failed: %s", e)
                return f"(Error: {e})"

    async def _consume_stream_events(
        self,
        result: Any,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None,
    ) -> tuple[str, BaseException | None]:
        full_text = ""
        try:
            response_delta_type = _get_response_delta_type()
            async for event in result.stream_events():
                delta = self._extract_stream_delta(event, response_delta_type)
                if delta is not None:
                    full_text += delta
                    await on_chunk(delta)
                elif event.type == "run_item_stream_event":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) == "tool_call_item" and on_tool_call:
                        raw_item = getattr(item, "raw_item", None)
                        await on_tool_call(str(getattr(raw_item, "name", "tool")))
            return full_text, None
        except BaseException as e:
            return full_text, e

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
        if hasattr(event_data, "delta"):
            return event_data.delta
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
        lock = self._background_lock if use_background_lock else self._user_lock
        async with lock:
            full_text = ""
            try:
                from agents import Runner

                result = Runner.run_streamed(agent, stripped, session=thread)
                full_text, stream_error = await self._consume_stream_events(
                    result=result,
                    on_chunk=on_chunk,
                    on_tool_call=on_tool_call,
                )
                if stream_error is not None:
                    raise stream_error
                return result.final_output or full_text
            except Exception as e:
                logger.exception("Agent streaming invocation failed: %s", e)
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
        async with self._background_lock:
            try:
                channel_id = turn_context.channel_id if turn_context else None
                result = await self._approval.run_with_approval_loop(
                    agent=agent,
                    input_or_state=stripped,
                    session=session,
                    channel_id=channel_id,
                )
                return result.final_output or ""
            except Exception as e:
                logger.exception("Agent background invocation failed: %s", e)
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

