"""MessageRouter: routes user messages to the agent and responses to channels.

Serializes agent invocations. Dispatches user_message and agent_response for middleware.
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from core.extensions.contract import ChannelProvider, StreamingChannelProvider, TurnContext
from core.events.topics import SystemTopics

if TYPE_CHECKING:
    from core.events.bus import EventBus

logger = logging.getLogger(__name__)


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
        self._invoke_middleware: Callable[[str, TurnContext], Awaitable[str]] | None = None
        self._session: Any = None
        self._session_id: str | None = None
        self._last_message_at: float | None = None
        self._session_timeout: int = 1800
        self._session_db_path: str | None = None
        self._event_bus: "EventBus | None" = None

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
            self._subscribers[event] = [h for h in self._subscribers[event] if h != handler]

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

    async def invoke_agent(
        self, prompt: str, turn_context: TurnContext | None = None
    ) -> str:
        """Run agent with prompt; return response. Serialized with other user invocations."""
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        async with self._user_lock:
            try:
                from agents import Runner

                result = await Runner.run(
                    agent,
                    stripped,
                    session=self._session,
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
        """Run agent with streaming callbacks; return final response.

        Lock is held for the entire stream to keep in-order responses across users.
        """
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)

        response_delta_type = None
        try:
            from openai.types.responses import ResponseTextDeltaEvent

            response_delta_type = ResponseTextDeltaEvent
        except Exception:
            response_delta_type = None

        full_text = ""
        async with self._user_lock:
            try:
                from agents import Runner

                result = Runner.run_streamed(
                    agent,
                    stripped,
                    session=self._session,
                )
                async for event in result.stream_events():
                    if event.type == "raw_response_event":
                        event_data = event.data
                        delta: str | None = None
                        if response_delta_type is not None and isinstance(
                            event_data, response_delta_type
                        ):
                            delta = event_data.delta
                        elif hasattr(event_data, "delta"):
                            delta = event_data.delta
                        if delta is not None:
                            full_text += delta
                            await on_chunk(delta)
                    elif (
                        event.type == "run_item_stream_event"
                        and getattr(getattr(event, "item", None), "type", None)
                        == "tool_call_item"
                    ):
                        event_item = getattr(event, "item", None)
                        raw_item = getattr(event_item, "raw_item", None)
                        tool_name = getattr(raw_item, "name", "tool")
                        if on_tool_call:
                            await on_tool_call(str(tool_name))
                return result.final_output or full_text
            except Exception as e:
                logger.exception("Agent streaming invocation failed: %s", e)
                if full_text:
                    error_chunk = f"\n(Error: {e})"
                    try:
                        await on_chunk(error_chunk)
                    except Exception:
                        logger.exception("Error callback failed while reporting stream error")
                    return full_text + error_chunk
                return f"(Error: {e})"

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
                from agents import Runner

                result = await Runner.run(
                    agent,
                    stripped,
                    session=session,
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
        """Run agent with streaming; uses background lock and ephemeral session.
        Does not block user messages."""
        if not self._agent:
            return "(No agent configured.)"
        agent, stripped = await self._prepare_agent(prompt, turn_context)
        session = self._get_background_session()

        response_delta_type = None
        try:
            from openai.types.responses import ResponseTextDeltaEvent

            response_delta_type = ResponseTextDeltaEvent
        except Exception:
            response_delta_type = None

        full_text = ""
        async with self._background_lock:
            try:
                from agents import Runner

                result = Runner.run_streamed(
                    agent,
                    stripped,
                    session=session,
                )
                async for event in result.stream_events():
                    if event.type == "raw_response_event":
                        event_data = event.data
                        delta: str | None = None
                        if response_delta_type is not None and isinstance(
                            event_data, response_delta_type
                        ):
                            delta = event_data.delta
                        elif hasattr(event_data, "delta"):
                            delta = event_data.delta
                        if delta is not None:
                            full_text += delta
                            await on_chunk(delta)
                    elif (
                        event.type == "run_item_stream_event"
                        and getattr(getattr(event, "item", None), "type", None)
                        == "tool_call_item"
                    ):
                        event_item = getattr(event, "item", None)
                        raw_item = getattr(event_item, "raw_item", None)
                        tool_name = getattr(raw_item, "name", "tool")
                        if on_tool_call:
                            await on_tool_call(str(tool_name))
                return result.final_output or full_text
            except Exception as e:
                logger.exception("Agent background streaming failed: %s", e)
                if full_text:
                    error_chunk = f"\n(Error: {e})"
                    try:
                        await on_chunk(error_chunk)
                    except Exception:
                        logger.exception("Error callback failed while reporting stream error")
                    return full_text + error_chunk
                return f"(Error: {e})"

    async def enrich_prompt(
        self, prompt: str, turn_context: TurnContext | None = None
    ) -> str:
        """Return context + separator + prompt for use as a single prompt by downstream agents.

        Used by extensions (e.g. Heartbeat Scout) that pass the result to their own agent
        as one message. For invoke_agent, context is injected into system role instead.
        """
        stripped = prompt.strip()
        if not self._invoke_middleware:
            return stripped
        ctx = turn_context or TurnContext(agent_id=self._agent_id)
        context = await self._invoke_middleware(stripped, ctx) or ""
        if context:
            return context + "\n\n---\n\n" + stripped
        return stripped

    async def handle_user_message(
        self,
        text: str,
        user_id: str,
        channel: ChannelProvider,
        channel_id: str,
    ) -> None:
        """Invoke agent with user message, send response via channel. Serialized."""
        now = time.time()
        if (
            self._last_message_at is not None
            and (now - self._last_message_at) > self._session_timeout
        ):
            await self._rotate_session()
        self._last_message_at = now

        turn_context = TurnContext(
            agent_id=self._agent_id,
            channel_id=channel_id,
            user_id=user_id,
            session_id=self._session_id,
        )

        await self._emit(
            "user_message",
            {"text": text, "user_id": user_id, "channel": channel, "session_id": self._session_id},
        )
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
        await self._emit(
            "agent_response",
            {
                "user_id": user_id,
                "text": response,
                "channel": channel,
                "session_id": self._session_id,
                "agent_id": self._agent_id,
            },
        )

    async def notify_user(self, text: str, channel_id: str | None = None) -> None:
        """Send proactive notification to user. Channel handles all addressing internally."""
        if not self._channels:
            logger.warning("notify_user: no channels registered")
            return
        if channel_id and channel_id in self._channels:
            ch = self._channels[channel_id]
        else:
            ch = next(iter(self._channels.values()))
        await ch.send_message(text)
