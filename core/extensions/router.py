"""MessageRouter: routes user messages to the agent and responses to channels.

Serializes agent invocations. Dispatches user_message and agent_response for middleware.
"""

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from core.extensions.contract import (
    ChannelProvider,
    StreamingChannelProvider,
    TurnContext,
)
from core.events.topics import SystemTopics

if TYPE_CHECKING:
    from core.events.bus import EventBus
    from core.events.models import Event

logger = logging.getLogger(__name__)


def _get_response_delta_type() -> type | None:
    """ResponseTextDeltaEvent type for stream delta detection; None if openai not available."""
    try:
        from openai.types.responses import ResponseTextDeltaEvent
        return ResponseTextDeltaEvent
    except Exception:
        return None


class MessageRouter:
    """Handles user_message -> agent -> channel; notify_user; pub/sub for middleware."""

    def __init__(self) -> None:
        self._agent: Any = None
        self._agent_id: str = "orchestrator"
        self._channels: dict[str, ChannelProvider] = {}
        self._channel_descriptions: dict[str, str] = {}
        self._user_lock = asyncio.Lock()
        self._background_lock = asyncio.Lock()
        self._subscribers: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self._invoke_middleware: Callable[[str, TurnContext], Awaitable[str]] | None = (
            None
        )
        self._session: Any = None
        self._session_id: str | None = None
        self._last_message_at: float | None = None
        self._session_timeout: int = 1800
        self._session_db_path: str | None = None
        self._event_bus: "EventBus | None" = None
        self._pending_approvals: dict[str, tuple[asyncio.Event, dict[str, Any]]] = {}
        self._approval_timeout: float = 60.0

    def set_agent(self, agent: Any, agent_id: str = "orchestrator") -> None:
        """Set the Orchestrator agent (called by runner after agent creation)."""
        self._agent = agent
        self._agent_id = agent_id

    def register_channel(self, ext_id: str, channel: ChannelProvider) -> None:
        """Register a channel. Called by Loader during protocol wiring."""
        self._channels[ext_id] = channel

    def get_channel(self, ext_id: str) -> ChannelProvider | None:
        """Return channel by extension id. Used when handling user.message events."""
        return self._channels.get(ext_id)

    def get_channel_ids(self) -> list[str]:
        """Return list of registered channel extension IDs."""
        return list(self._channels.keys())

    def set_channel_descriptions(self, descriptions: dict[str, str]) -> None:
        """Set human-readable channel descriptions (from manifest 'name' field)."""
        self._channel_descriptions = descriptions

    def get_channel_descriptions(self) -> dict[str, str]:
        """Return {channel_id: human-readable name} for all registered channels."""
        return self._channel_descriptions.copy()

    def subscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Subscribe to an internal event (e.g. user_message, agent_response)."""
        self._subscribers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Remove a previously registered subscription."""
        if event in self._subscribers:
            self._subscribers[event] = [
                h for h in self._subscribers[event] if h != handler
            ]

    def set_invoke_middleware(
        self,
        middleware: Callable[[str, TurnContext], Awaitable[str]],
    ) -> None:
        """Set middleware that returns context to inject into the system role.

        The callable receives (prompt, turn_context) and returns a context string. Empty string
        means no context. The caller injects this into system via agent.clone(instructions=...),
        not into the user message.
        """
        self._invoke_middleware = middleware

    def set_session(self, session: Any, session_id: str) -> None:
        """Set per-agent session for conversation history (short-term memory within a dialog)."""
        self._session = session
        self._session_id = session_id

    def configure_session(
        self,
        session_db_path: str,
        session_timeout: int,
        event_bus: "EventBus | None" = None,
    ) -> None:
        """Configure session lifecycle. Creates initial session. Called once by runner."""
        self._session_db_path = session_db_path
        self._session_timeout = session_timeout
        self._event_bus = event_bus
        self._session_id = f"orchestrator_{int(time.time())}"
        from agents import SQLiteSession

        self._session = SQLiteSession(self._session_id, session_db_path)
        if event_bus:
            event_bus.subscribe(
                SystemTopics.MCP_TOOL_APPROVAL_RESPONSE,
                self._on_mcp_approval_response,
                "kernel.router",
            )

    async def _on_mcp_approval_response(self, event: Any) -> None:
        """Handle MCP_TOOL_APPROVAL_RESPONSE: resolve pending approval waiters."""
        payload = event.payload if hasattr(event, "payload") else {}
        request_id = payload.get("request_id")
        if not request_id:
            return
        pending = self._pending_approvals.pop(request_id, None)
        if pending:
            evt, result = pending
            result["approved"] = payload.get("approved", False)
            result["reason"] = payload.get("reason")
            evt.set()

    async def _handle_one_approval_interruption(
        self,
        item: Any,
        channel_id: str | None,
        state: Any,
    ) -> None:
        """Publish MCP approval request, wait for response, approve or reject item on state."""
        request_id, evt = str(uuid.uuid4()), asyncio.Event()
        result_holder: dict[str, Any] = {}
        self._pending_approvals[request_id] = (evt, result_holder)
        tool_name = getattr(item, "tool_name", None) or getattr(item, "name", "?")
        args_str = str(getattr(item, "arguments", ""))
        try:
            if self._event_bus:
                await self._event_bus.publish(
                    SystemTopics.MCP_TOOL_APPROVAL_REQUEST,
                    "kernel.router",
                    {"request_id": request_id, "tool_name": tool_name, "arguments": args_str, "server_alias": "", "channel_id": channel_id},
                )
                try:
                    await asyncio.wait_for(evt.wait(), timeout=self._approval_timeout)
                except asyncio.TimeoutError:
                    logger.warning("MCP tool approval timed out for %s, rejecting", tool_name)
            else:
                result_holder["approved"] = False
            if result_holder.get("approved", False):
                state.approve(item)
            else:
                state.reject(item, always_reject=True)
        finally:
            self._pending_approvals.pop(request_id, None)

    async def _run_with_approval_loop(
        self,
        agent: Any,
        input_or_state: str | Any,
        session: Any,
        turn_context: TurnContext | None,
        max_rounds: int = 10,
    ) -> Any:
        """Run agent, handling MCP tool approval interruptions via EventBus."""
        from agents import Runner

        channel_id = turn_context.channel_id if turn_context else None
        result = await Runner.run(agent, input_or_state, session=session)
        rounds = 0
        while getattr(result, "interruptions", None) and rounds < max_rounds:
            rounds += 1
            state = result.to_state()
            for item in result.interruptions:
                await self._handle_one_approval_interruption(item, channel_id, state)
            result = await Runner.run(agent, state, session=session)
        return result

    async def _rotate_session(self) -> None:
        """Rotate to a new session and publish session.completed for the old one."""
        old_id = self._session_id
        self._session_id = f"orchestrator_{int(time.time())}"
        from agents import SQLiteSession

        if self._session_db_path is None:
            raise RuntimeError(
                "Session not configured: call configure_session before invoke"
            )
        self._session = SQLiteSession(self._session_id, self._session_db_path)
        if self._event_bus:
            await self._event_bus.publish(
                SystemTopics.SESSION_COMPLETED,
                "kernel",
                {"session_id": old_id, "reason": "inactivity_timeout"},
            )

    async def _emit(self, event: str, data: dict[str, Any]) -> None:
        """Dispatch event to subscribers."""
        for handler in self._subscribers.get(event, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.exception("Event handler error [%s]: %s", event, e)

    async def _consume_stream_events(
        self,
        result: Any,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None,
        response_delta_type: type | None,
    ) -> tuple[str, BaseException | None]:
        """Consume stream events from Runner.run_streamed result.
        Returns (accumulated_text, error). error is non-None if stream raised."""
        full_text = ""
        try:
            async for event in result.stream_events():
                delta = self._get_stream_delta(event, response_delta_type)
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

    def _get_stream_delta(
        self, event: Any, response_delta_type: type | None
    ) -> str | None:
        """Extract text delta from raw_response_event; None for other event types."""
        if event.type != "raw_response_event":
            return None
        event_data = getattr(event, "data", None)
        if response_delta_type is not None and isinstance(event_data, response_delta_type):
            return getattr(event_data, "delta", None)
        if hasattr(event_data, "delta"):
            return event_data.delta
        return None

    async def _prepare_agent(
        self,
        prompt: str,
        turn_context: TurnContext | None = None,
    ) -> tuple[Any, str]:
        """Run middleware, clone agent with context if needed. Returns (agent, stripped_prompt)."""
        ctx = turn_context or TurnContext(agent_id=self._agent_id)
        stripped = prompt.strip()
        context = ""
        if self._invoke_middleware:
            context = await self._invoke_middleware(stripped, ctx) or ""
        agent = self._agent
        if context and isinstance(getattr(self._agent, "instructions", None), str):
            agent = self._agent.clone(
                instructions=self._agent.instructions + "\n\n---\n\n" + context
            )
        return agent, stripped

    async def _run_streamed_invoke(
        self,
        agent: Any,
        stripped: str,
        session: Any,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None,
        use_background_lock: bool,
    ) -> str:
        """Run streamed agent under user or background lock. Returns final text or error message."""
        lock = self._background_lock if use_background_lock else self._user_lock
        full_text = ""
        async with lock:
            try:
                from agents import Runner
                result = Runner.run_streamed(agent, stripped, session=session)
                full_text, stream_error = await self._consume_stream_events(
                    result, on_chunk, on_tool_call, _get_response_delta_type()
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
                        logger.exception("Error callback failed while reporting stream error")
                    return full_text + f"\n(Error: {e})"
                return f"(Error: {e})"

    async def invoke_agent(
        self, prompt: str, turn_context: TurnContext | None = None
    ) -> str:
        """Run agent with prompt; return response. Serialized with other user invocations."""
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        async with self._user_lock:
            try:
                result = await self._run_with_approval_loop(
                    agent, stripped, self._session, turn_context
                )
                return result.final_output or ""
            except Exception as e:
                logger.exception("Agent invocation failed: %s", e)
                return f"(Error: {e})"

    async def invoke_agent_streamed(
        self,
        prompt: str,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None = None,
        turn_context: TurnContext | None = None,
    ) -> str:
        """Run agent with streaming callbacks; return final response."""
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        return await self._run_streamed_invoke(
            agent, stripped, self._session, on_chunk, on_tool_call, False
        )

    def _get_background_session(self) -> Any:
        """Ephemeral session for background invocations; does not share user conversation history."""
        if self._session_db_path is None:
            return None
        from agents import SQLiteSession

        session_id = f"background_{int(time.time())}"
        return SQLiteSession(session_id, self._session_db_path)

    async def invoke_agent_background(
        self, prompt: str, turn_context: TurnContext | None = None
    ) -> str:
        """Run agent with prompt; uses background lock and ephemeral session.
        Does not block user messages. Used by EventBus handlers (system.agent.task, system.agent.background)."""
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        session = self._get_background_session()
        async with self._background_lock:
            try:
                result = await self._run_with_approval_loop(
                    agent, stripped, session, turn_context
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
        """Run agent with streaming; uses background lock and ephemeral session."""
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        session = self._get_background_session()
        return await self._run_streamed_invoke(
            agent, stripped, session, on_chunk, on_tool_call, True
        )

    async def enrich_prompt(
        self, prompt: str, turn_context: TurnContext | None = None
    ) -> str:
        """Return context + separator + prompt for use as a single prompt by downstream agents.

        Extensions that run their own Agent pass the result as one message.
        For invoke_agent, context is injected into system role instead.
        """
        stripped = prompt.strip()
        if not self._invoke_middleware:
            return stripped
        ctx = turn_context or TurnContext(agent_id=self._agent_id)
        context = await self._invoke_middleware(stripped, ctx) or ""
        if context:
            return context + "\n\n---\n\n" + stripped
        return stripped

    async def _maybe_rotate_session(self) -> None:
        """Rotate session if last message was longer than session_timeout ago."""
        now = time.time()
        if (
            self._last_message_at is not None
            and (now - self._last_message_at) > self._session_timeout
        ):
            await self._rotate_session()
        self._last_message_at = now

    async def _deliver_response_to_channel(
        self,
        channel: ChannelProvider,
        user_id: str,
        text: str,
        turn_context: TurnContext,
    ) -> str:
        """Invoke agent and send response via channel (streaming or non-streaming). Returns response text."""
        if isinstance(channel, StreamingChannelProvider):
            await channel.on_stream_start(user_id)
            response = await self.invoke_agent_streamed(
                text,
                on_chunk=lambda chunk: channel.on_stream_chunk(user_id, chunk),
                on_tool_call=lambda name: channel.on_stream_status(
                    user_id, f"Using: {name}"
                ),
                turn_context=turn_context,
            )
            await channel.on_stream_end(user_id, response)
        else:
            response = await self.invoke_agent(text, turn_context)
            await channel.send_to_user(user_id, response)
        return response

    async def handle_user_message(
        self,
        text: str,
        user_id: str,
        channel: ChannelProvider,
        channel_id: str,
    ) -> None:
        """Invoke agent with user message, send response via channel. Serialized."""
        await self._maybe_rotate_session()
        turn_context = TurnContext(
            agent_id=self._agent_id,
            channel_id=channel_id,
            user_id=user_id,
            session_id=self._session_id,
        )
        await self._emit("user_message", {"text": text, "user_id": user_id, "channel": channel, "session_id": self._session_id})
        response = await self._deliver_response_to_channel(
            channel, user_id, text, turn_context
        )
        await self._emit("agent_response", {"user_id": user_id, "text": response, "channel": channel, "session_id": self._session_id, "agent_id": self._agent_id})

    async def notify_user(self, text: str, channel_id: str | None = None) -> None:
        """Send proactive notification to user. Channel handles all addressing internally."""
        if not self._channels:
            logger.warning("notify_user: no channels registered")
            return
        ch = self._channels.get(channel_id) if channel_id else None
        if ch is None:
            ch = next(iter(self._channels.values()))
        await ch.send_message(text)
