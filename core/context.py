"""ExtensionContext: kernel API for extensions. Single entry point; no direct core imports in extensions."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable

from core.router import MessageRouter


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
    ) -> None:
        self.extension_id = extension_id
        self.config = config
        self.logger = logger
        self._router = router
        self._get_extension = get_extension
        self._data_dir_path = data_dir_path
        self._shutdown_event = shutdown_event
        self.on_user_message = self._router.handle_user_message

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
