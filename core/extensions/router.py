"""MessageRouter: routes user messages to the agent and responses to channels.

Serializes agent invocations. Dispatches user_message and agent_response for middleware.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

from core.extensions.contract import ChannelProvider

logger = logging.getLogger(__name__)


class MessageRouter:
    """Handles user_message -> agent -> channel; notify_user; pub/sub for middleware."""

    def __init__(self) -> None:
        self._agent: Any = None
        self._agent_id: str = "orchestrator"
        self._channels: dict[str, ChannelProvider] = {}
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self._invoke_middleware: Callable[[str, str | None], Awaitable[str]] | None = None
        self._session: Any = None
        self._session_id: str | None = None

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

    def subscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Subscribe to an internal event (e.g. user_message, agent_response)."""
        self._subscribers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Remove a previously registered subscription."""
        if event in self._subscribers:
            self._subscribers[event] = [h for h in self._subscribers[event] if h != handler]

    def set_invoke_middleware(
        self,
        middleware: Callable[[str, str | None], Awaitable[str]],
    ) -> None:
        """Set middleware to enrich prompt before agent invocation. Called before Runner.run()."""
        self._invoke_middleware = middleware

    def set_session(self, session: Any, session_id: str) -> None:
        """Set per-agent session for conversation history (short-term memory within a dialog)."""
        self._session = session
        self._session_id = session_id

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

    async def invoke_agent(self, prompt: str, agent_id: str | None = None) -> str:
        """Run agent with prompt; return response. Serialized with other invocations."""
        if not self._agent:
            return "(No agent configured.)"
        if self._invoke_middleware:
            prompt = await self._invoke_middleware(prompt.strip(), agent_id)
        async with self._lock:
            try:
                from agents import Runner

                result = await Runner.run(
                    self._agent,
                    prompt,
                    session=self._session,
                )
                return result.final_output or ""
            except Exception as e:
                logger.exception("Agent invocation failed: %s", e)
                return f"(Error: {e})"

    async def handle_user_message(
        self, text: str, user_id: str, channel: ChannelProvider
    ) -> None:
        """Invoke agent with user message, send response via channel. Serialized."""
        await self._emit(
            "user_message",
            {"text": text, "user_id": user_id, "channel": channel, "session_id": self._session_id},
        )
        response = await self.invoke_agent(text)
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
        await channel.send_to_user(user_id, response)

    async def notify_user(self, text: str, channel_id: str | None = None) -> None:
        """Send notification to user. Single-user: kernel picks active channel."""
        if not self._channels:
            logger.warning("notify_user: no channels registered")
            return
        if channel_id and channel_id in self._channels:
            ch = self._channels[channel_id]
        else:
            ch = next(iter(self._channels.values()))
        user_id = "default"
        await ch.send_to_user(user_id, text)
