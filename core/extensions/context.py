"""ExtensionContext: kernel API for extensions. Single entry point; no direct core imports in extensions."""

import asyncio
import logging
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from core.extensions.router import MessageRouter

if TYPE_CHECKING:
    from core.events.bus import EventBus
    from core.events.models import Event


class ExtensionContext:
    """Everything an extension can do — only through this object."""

    def __init__(
        self,
        extension_id: str,
        config: dict[str, Any],
        logger: logging.Logger,
        router: MessageRouter,
        get_extension: Callable[[str], Any],
        data_dir_path: Path,
        shutdown_event: asyncio.Event | None,
        resolved_tools: list[Any] | None = None,
        resolved_instructions: str = "",
        agent_model: str = "",
        model_router: Any = None,
        agent_id: str | None = None,
        event_bus: "EventBus | None" = None,
    ) -> None:
        self.extension_id = extension_id
        self.config = config
        self.logger = logger
        self._router = router
        self._get_extension = get_extension
        self._data_dir_path = data_dir_path
        self._shutdown_event = shutdown_event
        self.resolved_tools: list[Any] = resolved_tools or []
        self.resolved_instructions: str = resolved_instructions
        self.agent_model: str = agent_model
        self._model_router = model_router
        self.agent_id: str | None = agent_id or extension_id
        self._event_bus = event_bus
        self.on_user_message = self._router.handle_user_message

    @property
    def model_router(self) -> Any:
        """ModelRouter for get_model(agent_id). None if not set (e.g. legacy runner)."""
        return self._model_router

    async def notify_user(
        self, text: str, channel_id: str | None = None
    ) -> None:
        """Send notification to user. Single-user app — kernel resolves channel."""
        await self._router.notify_user(text, channel_id)

    async def invoke_agent(self, prompt: str) -> str:
        """Ask the agent to process a prompt and return a response."""
        return await self._router.invoke_agent(prompt)

    def subscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Subscribe to an internal event (e.g. user_message, agent_response)."""
        self._router.subscribe(event, handler)

    def unsubscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Remove a previously registered subscription."""
        self._router.unsubscribe(event, handler)

    async def emit(
        self,
        topic: str,
        payload: dict,
        correlation_id: str | None = None,
    ) -> None:
        """Publish event to the Event Bus. Fire-and-forget."""
        if self._event_bus:
            await self._event_bus.publish(topic, self.extension_id, payload, correlation_id)

    async def schedule_at(
        self,
        delay: float | timedelta,
        topic: str,
        payload: dict,
        correlation_id: str | None = None,
    ) -> int | None:
        """Schedule event to fire after delay seconds (or timedelta). Returns deferred_id or None."""
        if self._event_bus:
            if isinstance(delay, timedelta):
                delay = delay.total_seconds()
            fire_at = time.time() + delay
            return await self._event_bus.schedule_at(
                fire_at=fire_at,
                topic=topic,
                payload=payload,
                source=self.extension_id,
                correlation_id=correlation_id,
            )
        return None

    def subscribe_event(
        self,
        topic: str,
        handler: Callable[["Event"], Awaitable[None]],
    ) -> None:
        """Subscribe to durable events via the Event Bus."""
        if self._event_bus:
            self._event_bus.subscribe(topic, handler, self.extension_id)

    async def get_secret(self, name: str) -> str | None:
        """Get a secret by name from .env."""
        return os.environ.get(name)

    def get_config(self, key: str, default: Any = None) -> Any:
        """Read a value from the config: block in manifest.yaml."""
        return self.config.get(key, default)

    def get_extension(self, extension_id: str) -> Any:
        """Get an instance of another extension (only from depends_on)."""
        return self._get_extension(extension_id)

    @property
    def data_dir(self) -> Path:
        """Private extension folder: sandbox/data/<extension_id>/."""
        self._data_dir_path.mkdir(parents=True, exist_ok=True)
        return self._data_dir_path

    def request_restart(self) -> None:
        """Ask supervisor to restart the kernel. Writes sandbox/.restart_requested."""
        restart_file = self._data_dir_path.parent.parent / ".restart_requested"
        restart_file.parent.mkdir(parents=True, exist_ok=True)
        restart_file.write_text("restart requested", encoding="utf-8")

    def request_shutdown(self) -> None:
        """Shut down the application."""
        if self._shutdown_event:
            self._shutdown_event.set()
