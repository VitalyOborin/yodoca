"""Event wiring between EventBus topics and kernel handlers."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from core.events import EventBus
from core.events.models import Event
from core.events.topics import SystemTopics
from core.extensions.contract import (
    AgentInvocationContext,
    AgentProvider,
    Extension,
    ExtensionState,
    TurnContext,
)
from core.extensions.manifest import ExtensionManifest
from core.extensions.manifest_utils import iter_active_manifests
from core.extensions.routing.router import MessageRouter

if TYPE_CHECKING:
    from core.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)


class EventWiringManager:
    """Wire manifest-driven event handlers to EventBus."""

    def __init__(
        self,
        router: MessageRouter | None,
        manifests: list[ExtensionManifest],
        state: dict[str, ExtensionState],
        extensions: dict[str, Extension],
        agent_registry: "AgentRegistry | None",
    ) -> None:
        self._router = router
        self._manifests = manifests
        self._state = state
        self._extensions = extensions
        self._agent_registry = agent_registry
        self._agent_tasks: set[asyncio.Task[Any]] = set()

    def _collect_proactive_subscriptions(self) -> dict[str, str]:
        """Return {topic: ext_id} for invoke_agent subscriptions."""
        result: dict[str, str] = {}
        for ext_id, manifest in iter_active_manifests(self._manifests, self._state):
            if not manifest.events or not manifest.events.subscribes:
                continue
            if not self._agent_registry or self._agent_registry.get(ext_id) is None:
                continue
            for sub in manifest.events.subscribes:
                if sub.handler != "invoke_agent":
                    continue
                if sub.topic not in result:
                    result[sub.topic] = ext_id
        return result

    async def _on_user_notify(self, event: Event) -> None:
        if self._router:
            await self._router.notify_user(
                event.payload.get("text", ""),
                event.payload.get("channel_id"),
            )

    async def _on_agent_task(self, event: Event) -> None:
        if not self._router:
            return
        router = self._router
        prompt = event.payload.get("prompt", "")
        channel_id = event.payload.get("channel_id")
        correlation_id = event.payload.get("correlation_id") or event.correlation_id

        async def _run_agent_task() -> None:
            started_at = time.perf_counter()
            logger.info(
                "agent task: start",
                extra={
                    "event_id": event.id,
                    "correlation_id": correlation_id,
                    "prompt_len": len(prompt),
                },
            )
            try:
                turn_context = TurnContext(
                    agent_id="orchestrator",
                    channel_id=channel_id,
                )
                response = await router.invoke_agent_background(
                    prompt, turn_context=turn_context
                )
                if response:
                    await router.notify_user(response, channel_id)
                duration_ms = int((time.perf_counter() - started_at) * 1000)
                logger.info(
                    "agent task: done",
                    extra={
                        "event_id": event.id,
                        "correlation_id": correlation_id,
                        "duration_ms": duration_ms,
                    },
                )
            except Exception as e:
                logger.exception(
                    "agent task: failed event_id=%s correlation_id=%s: %s",
                    event.id,
                    correlation_id,
                    e,
                )

        task = asyncio.create_task(_run_agent_task())
        self._agent_tasks.add(task)
        task.add_done_callback(self._agent_tasks.discard)

    async def _on_agent_background(self, event: Event) -> None:
        if not self._router:
            return
        prompt = event.payload.get("prompt", "")
        correlation_id = event.payload.get("correlation_id") or event.correlation_id
        started_at = time.perf_counter()
        logger.info(
            "agent loop: start",
            extra={
                "correlation_id": correlation_id,
                "event_id": event.id,
                "prompt_len": len(prompt),
            },
        )
        try:
            await self._router.invoke_agent_background(prompt)
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info(
                "agent loop: done",
                extra={
                    "correlation_id": correlation_id,
                    "event_id": event.id,
                    "duration_ms": duration_ms,
                },
            )
        except Exception as e:
            logger.exception("agent loop: failed: %s", e)
            raise

    def _wire_system_topics(self, event_bus: EventBus) -> None:
        """Register guaranteed system topic handlers. Called before extension wiring."""
        event_bus.subscribe(
            SystemTopics.USER_NOTIFY, self._on_user_notify, "kernel.system"
        )
        event_bus.subscribe(
            SystemTopics.AGENT_TASK, self._on_agent_task, "kernel.system"
        )
        event_bus.subscribe(
            SystemTopics.AGENT_BACKGROUND,
            self._on_agent_background,
            "kernel.system",
        )

    def _wire_notify_user_handlers(self, event_bus: EventBus) -> None:
        for ext_id, manifest in iter_active_manifests(self._manifests, self._state):
            if not manifest.events or not manifest.events.subscribes:
                continue
            for sub in manifest.events.subscribes:
                if sub.handler != "notify_user":
                    continue

                topic = sub.topic
                subscriber_id = ext_id

                async def handler(
                    event: Event,
                    _topic: str = topic,
                    _subscriber_id: str = subscriber_id,
                ) -> None:
                    router = self._router
                    if router is None:
                        return
                    await router.notify_user(
                        event.payload.get("text", ""),
                        event.payload.get("channel_id"),
                    )

                event_bus.subscribe(topic, handler, subscriber_id)

    async def _on_kernel_user_message(self, event: Event) -> None:
        if not self._router:
            return
        router = self._router
        text = event.payload.get("text", "").strip()
        user_id = event.payload.get("user_id", "default")
        channel_id = event.payload.get("channel_id")
        thread_id = event.payload.get("thread_id")
        if not text or not channel_id:
            logger.warning("user.message missing text or channel_id: %s", event.payload)
            return
        channel = router.get_channel(channel_id)
        if not channel:
            logger.warning("user.message: unknown channel_id %s", channel_id)
            return
        await router.handle_user_message(
            text,
            user_id,
            channel,
            channel_id,
            event_id=event.id,
            thread_id=thread_id,
        )

    def _make_proactive_handler(
        self, topic: str, ext_id: str, agent: AgentProvider
    ) -> Callable[[Event], Any]:
        router = self._router

        async def handler(event: Event) -> None:
            task = (
                event.payload.get("prompt")
                or f"Background event '{topic}': {event.payload}"
            )
            context = AgentInvocationContext(correlation_id=event.correlation_id)
            try:
                response = await agent.invoke(task, context)
                if response.status == "success" and response.content and router:
                    await router.notify_user(
                        response.content, event.payload.get("channel_id")
                    )
                elif response.status != "success":
                    logger.debug(
                        "Proactive handler for %s: agent returned %s",
                        topic,
                        response.status,
                    )
            except Exception as e:
                logger.exception("Proactive handler for %s failed: %s", topic, e)

        return handler

    def _wire_proactive_handlers(self, event_bus: EventBus) -> None:
        proactive_map = self._collect_proactive_subscriptions()
        for topic, ext_id in proactive_map.items():
            pair = self._agent_registry.get(ext_id) if self._agent_registry else None
            agent = pair[1] if pair else None
            if not agent:
                logger.debug(
                    "Proactive topic %s: ext %s is not AgentProvider, skip",
                    topic,
                    ext_id,
                )
                continue
            handler = self._make_proactive_handler(topic, ext_id, agent)
            event_bus.subscribe(topic, handler, "kernel.proactive")

    def wire(self, event_bus: EventBus) -> None:
        """Wire manifest-driven notify_user and invoke_agent handlers."""
        self._wire_system_topics(event_bus)
        self._wire_notify_user_handlers(event_bus)
        event_bus.subscribe("user.message", self._on_kernel_user_message, "kernel")
        self._wire_proactive_handlers(event_bus)
