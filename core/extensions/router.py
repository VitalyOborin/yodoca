"""MessageRouter: routes user messages to agent invocations and channel delivery."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from core.extensions.agent_invoker import AgentInvoker
from core.extensions.approval_coordinator import ApprovalCoordinator
from core.extensions.contract import ChannelProvider, TurnContext
from core.extensions.response_delivery_service import ResponseDeliveryService
from core.extensions.session_manager import SessionManager

if TYPE_CHECKING:
    from core.events.bus import EventBus

logger = logging.getLogger(__name__)


class MessageRouter:
    """Coordinates channels, agent invocation, and event emission."""

    def __init__(self) -> None:
        self._channels: dict[str, ChannelProvider] = {}
        self._channel_descriptions: dict[str, str] = {}
        self._subscribers: dict[str, list[Callable[..., Any]]] = defaultdict(list)

        self._sessions = SessionManager()
        self._approval = ApprovalCoordinator(approval_timeout=60.0)
        self._invoker = AgentInvoker(
            approval_coordinator=self._approval,
            session_manager=self._sessions,
        )
        self._delivery = ResponseDeliveryService(invoker=self._invoker)

        # Compatibility mirrors for existing tests and integrations.
        self._event_bus: EventBus | None = None

    @property
    def _session(self) -> Any:
        return self._sessions.session

    @property
    def _session_id(self) -> str | None:
        return self._sessions.session_id

    def set_agent(self, agent: Any, agent_id: str = "orchestrator") -> None:
        self._invoker.set_agent(agent, agent_id=agent_id)

    def register_channel(self, ext_id: str, channel: ChannelProvider) -> None:
        self._channels[ext_id] = channel

    def get_channel(self, ext_id: str) -> ChannelProvider | None:
        return self._channels.get(ext_id)

    def get_channel_ids(self) -> list[str]:
        return list(self._channels.keys())

    def set_channel_descriptions(self, descriptions: dict[str, str]) -> None:
        self._channel_descriptions = descriptions

    def get_channel_descriptions(self) -> dict[str, str]:
        return self._channel_descriptions.copy()

    def subscribe(self, event: str, handler: Callable[..., Any]) -> None:
        self._subscribers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable[..., Any]) -> None:
        if event in self._subscribers:
            self._subscribers[event] = [
                registered for registered in self._subscribers[event] if registered != handler
            ]

    def set_invoke_middleware(
        self,
        middleware: Callable[[str, TurnContext], Awaitable[str]],
    ) -> None:
        self._invoker.middleware = middleware

    def set_session(self, session: Any, session_id: str) -> None:
        self._sessions.set_session(session, session_id)

    def list_session_ids(self) -> list[str]:
        """List session IDs in the pool (for web channel /api/conversations)."""
        return self._sessions.list_session_ids()

    def delete_session(self, session_id: str) -> bool:
        """Remove a session from the pool. Returns True if it existed."""
        return self._sessions.delete_session(session_id)

    def configure_session(
        self,
        session_db_path: str,
        session_timeout: int,
        event_bus: "EventBus | None" = None,
    ) -> None:
        self._event_bus = event_bus
        self._sessions.configure_session(
            session_db_path=session_db_path,
            session_timeout=session_timeout,
            event_bus=event_bus,
        )
        if event_bus:
            self._approval.bind_event_bus(event_bus)

    async def _emit(self, event: str, data: dict[str, Any]) -> None:
        for handler in self._subscribers.get(event, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.exception("Event handler error [%s]: %s", event, e)

    async def invoke_agent(
        self,
        prompt: str,
        turn_context: TurnContext | None = None,
    ) -> str:
        return await self._invoker.invoke_agent(prompt, turn_context=turn_context)

    async def invoke_agent_streamed(
        self,
        prompt: str,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None = None,
        turn_context: TurnContext | None = None,
    ) -> str:
        return await self._invoker.invoke_agent_streamed(
            prompt=prompt,
            on_chunk=on_chunk,
            on_tool_call=on_tool_call,
            turn_context=turn_context,
        )

    async def invoke_agent_background(
        self,
        prompt: str,
        turn_context: TurnContext | None = None,
    ) -> str:
        return await self._invoker.invoke_agent_background(
            prompt,
            turn_context=turn_context,
        )

    async def invoke_agent_background_streamed(
        self,
        prompt: str,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None = None,
        turn_context: TurnContext | None = None,
    ) -> str:
        return await self._invoker.invoke_agent_background_streamed(
            prompt=prompt,
            on_chunk=on_chunk,
            on_tool_call=on_tool_call,
            turn_context=turn_context,
        )

    async def enrich_prompt(
        self,
        prompt: str,
        turn_context: TurnContext | None = None,
    ) -> str:
        return await self._invoker.enrich_prompt(prompt, turn_context=turn_context)

    async def _rotate_session(self) -> None:
        await self._sessions.rotate_session()

    async def _maybe_rotate_session(self) -> None:
        await self._sessions.maybe_rotate()

    async def handle_user_message(
        self,
        text: str,
        user_id: str,
        channel: ChannelProvider,
        channel_id: str,
        event_id: int | None = None,
        session_id: str | None = None,
    ) -> None:
        if event_id is not None and self._event_bus:
            if await self._event_bus.is_user_message_completed(event_id):
                logger.debug(
                    "user.message event %s already processed, skipping duplicate",
                    event_id,
                )
                return

        if session_id is not None:
            session = self._sessions.get_or_create_session(session_id)
            effective_session_id = session_id
        else:
            await self._maybe_rotate_session()
            session = self._sessions.session
            effective_session_id = self._sessions.session_id

        turn_context = TurnContext(
            agent_id=self._invoker.agent_id,
            channel_id=channel_id,
            user_id=user_id,
            session_id=effective_session_id,
        )
        await self._emit(
            "user_message",
            {
                "text": text,
                "user_id": user_id,
                "channel": channel,
                "session_id": effective_session_id,
            },
        )
        response = await self._delivery.deliver(
            channel=channel,
            user_id=user_id,
            text=text,
            turn_context=turn_context,
            session=session,
        )
        await self._emit(
            "agent_response",
            {
                "user_id": user_id,
                "text": response,
                "channel": channel,
                "session_id": effective_session_id,
                "agent_id": self._invoker.agent_id,
            },
        )
        if event_id is not None and self._event_bus:
            await self._event_bus.record_user_message_completed(event_id)

    async def notify_user(self, text: str, channel_id: str | None = None) -> None:
        if not self._channels:
            logger.warning("notify_user: no channels registered")
            return
        channel = self._channels.get(channel_id) if channel_id else None
        if channel is None:
            channel = next(iter(self._channels.values()))
        await channel.send_message(text)
