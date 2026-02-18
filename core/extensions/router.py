"""MessageRouter: routes user messages to the agent and responses to channels.

Serializes agent invocations. Dispatches user_message and agent_response for middleware.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable

from core.extensions.contract import ChannelProvider

logger = logging.getLogger(__name__)


class MessageRouter:
    """Handles user_message -> agent -> channel; notify_user; pub/sub for middleware."""

    def __init__(self) -> None:
        self._agent: Any = None
        self._channels: dict[str, ChannelProvider] = {}
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def set_agent(self, agent: Any) -> None:
        """Set the Orchestrator agent (called by runner after agent creation)."""
        self._agent = agent

    def register_channel(self, ext_id: str, channel: ChannelProvider) -> None:
        """Register a channel. Called by Loader during protocol wiring."""
        self._channels[ext_id] = channel

    def subscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Subscribe to an internal event (e.g. user_message, agent_response)."""
        self._subscribers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Remove a previously registered subscription."""
        if event in self._subscribers:
            self._subscribers[event] = [h for h in self._subscribers[event] if h != handler]

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

    async def invoke_agent(self, prompt: str) -> str:
        """Run agent with prompt; return response. Serialized with other invocations."""
        if not self._agent:
            return "(No agent configured.)"
        async with self._lock:
            try:
                from agents import Runner

                result = await Runner.run(self._agent, prompt.strip())
                return result.final_output or ""
            except Exception as e:
                logger.exception("Agent invocation failed: %s", e)
                return f"(Error: {e})"

    async def handle_user_message(
        self, text: str, user_id: str, channel: ChannelProvider
    ) -> None:
        """Invoke agent with user message, send response via channel. Serialized."""
        await self._emit("user_message", {"text": text, "user_id": user_id, "channel": channel})
        response = await self.invoke_agent(text)
        await self._emit("agent_response", {"user_id": user_id, "text": response, "channel": channel})
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
