"""MessageRouter: routes user messages to agent invocations and channel delivery."""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from core.extensions.contract import ChannelProvider, TurnContext
from core.extensions.persistence.project_repository import ProjectRepository
from core.extensions.persistence.project_service import ProjectService
from core.extensions.persistence.thread_manager import ThreadManager
from core.extensions.routing.agent_invoker import AgentInvoker
from core.extensions.routing.approval_coordinator import ApprovalCoordinator
from core.extensions.routing.response_delivery import ResponseDeliveryService

if TYPE_CHECKING:
    from core.events.bus import EventBus

logger = logging.getLogger(__name__)


class MessageRouter:
    """Coordinates channels, agent invocation, and event emission."""

    def __init__(
        self,
        *,
        thread_manager: ThreadManager | None = None,
        approval_coordinator: ApprovalCoordinator | None = None,
        agent_invoker: AgentInvoker | None = None,
        response_delivery: ResponseDeliveryService | None = None,
        project_service: ProjectService | None = None,
    ) -> None:
        self._channels: dict[str, ChannelProvider] = {}
        self._channel_descriptions: dict[str, str] = {}
        self._subscribers: dict[str, list[Callable[..., Any]]] = defaultdict(list)

        self._threads = thread_manager or ThreadManager()
        self._approval = approval_coordinator or ApprovalCoordinator(
            approval_timeout=60.0
        )
        self._invoker = agent_invoker or AgentInvoker(
            approval_coordinator=self._approval,
            thread_manager=self._threads,
        )
        self._delivery = response_delivery or ResponseDeliveryService(
            invoker=self._invoker
        )
        self._project_service = project_service

        # Compatibility mirrors for existing tests and integrations.
        self._event_bus: EventBus | None = None

    @property
    def _session(self) -> Any:
        return self._threads.thread

    @property
    def _thread_id(self) -> str | None:
        return self._threads.thread_id

    @property
    def thread_manager(self) -> ThreadManager:
        return self._threads

    @property
    def project_service(self) -> ProjectService | None:
        return self._project_service

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
                registered
                for registered in self._subscribers[event]
                if registered != handler
            ]

    def set_invoke_middleware(
        self,
        middleware: Callable[[str, TurnContext], Awaitable[str]],
    ) -> None:
        self._invoker.middleware = middleware

    def set_thread(self, thread: Any, thread_id: str) -> None:
        self._threads.set_thread(thread, thread_id)

    def configure_thread(
        self,
        thread_db_path: str,
        thread_timeout: int,
        event_bus: "EventBus | None" = None,
    ) -> None:
        self._event_bus = event_bus
        self._threads.configure_thread(
            thread_db_path=thread_db_path,
            thread_timeout=thread_timeout,
            event_bus=event_bus,
        )
        self._project_service = ProjectService(
            ProjectRepository(thread_db_path),
            self._threads.thread_repository,
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

    async def _rotate_thread(self) -> None:
        await self._threads.rotate_thread()

    async def _maybe_rotate_thread(self) -> None:
        await self._threads.maybe_rotate()

    async def handle_user_message(
        self,
        text: str,
        user_id: str,
        channel: ChannelProvider,
        channel_id: str,
        event_id: int | None = None,
        thread_id: str | None = None,
    ) -> None:
        if event_id is not None and self._event_bus:
            if await self._event_bus.is_user_message_completed(event_id):
                logger.debug(
                    "user.message event %s already processed, skipping duplicate",
                    event_id,
                )
                return

        effective_thread_id: str | None
        if thread_id is not None:
            thread = self._threads.get_or_create_thread(
                thread_id, channel_id=channel_id
            )
            effective_thread_id = thread_id
        else:
            await self._maybe_rotate_thread()
            thread = self._threads.thread
            effective_thread_id = self._threads.thread_id
        if effective_thread_id is None:
            raise RuntimeError("Thread ID was not initialized")
        self._threads.touch_thread(
            effective_thread_id,
            channel_id=channel_id,
            now_ts=int(time.time()),
        )

        turn_context = TurnContext(
            agent_id=self._invoker.agent_id,
            channel_id=channel_id,
            user_id=user_id,
            thread_id=effective_thread_id,
        )
        await self._emit(
            "user_message",
            {
                "text": text,
                "user_id": user_id,
                "channel": channel,
                "thread_id": effective_thread_id,
            },
        )
        response = await self._delivery.deliver(
            channel=channel,
            user_id=user_id,
            text=text,
            turn_context=turn_context,
            session=thread,
        )
        await self._emit(
            "agent_response",
            {
                "user_id": user_id,
                "text": response,
                "channel": channel,
                "thread_id": effective_thread_id,
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
