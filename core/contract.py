"""Extension protocols: base contract and capability interfaces.

Loader detects capabilities via isinstance(ext, Protocol). No type field in manifest.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Extension(Protocol):
    """Base contract: identity and lifecycle. Required for all extensions."""

    id: str  # matches folder name, e.g. "telegram_channel"
    name: str
    version: str

    async def initialize(self, context: "ExtensionContext") -> None:
        """Called once on load. Subscriptions, dependency init."""

    async def start(self) -> None:
        """Start active work: polling loops, servers, background tasks."""

    async def stop(self) -> None:
        """Graceful shutdown. Cancel tasks, close connections."""

    async def destroy(self) -> None:
        """Release resources. Called after stop()."""

    def health_check(self) -> bool:
        """True = operating normally."""


@runtime_checkable
class ToolProvider(Protocol):
    """Provides callable tools for the AI agent."""

    def get_tools(self) -> list[Any]:
        """List of @function_tool objects for the agent."""


@runtime_checkable
class ChannelProvider(Protocol):
    """User communication channel. Receives messages and sends responses."""

    async def send_to_user(self, user_id: str, message: str) -> None:
        """Send agent response to user through this channel."""


@runtime_checkable
class ServiceProvider(Protocol):
    """Runs a background service."""

    async def run_background(self) -> None:
        """Main service loop. Must handle CancelledError."""


@runtime_checkable
class SchedulerProvider(Protocol):
    """Periodic task by cron. Can return alert to notify user."""

    def get_schedule(self) -> str:
        """Cron expression, e.g. '*/5 * * * *'."""

    async def execute(self) -> dict[str, Any] | None:
        """Run the task. Return {'text': '...'} to notify user."""


@runtime_checkable
class SetupProvider(Protocol):
    """Extension that needs configuration (secrets, settings)."""

    def get_setup_schema(self) -> list[dict]:
        """[{name, description, secret, required}] — list of setup parameters."""

    async def apply_config(self, name: str, value: str) -> None:
        """Save config value. Extension decides where to store it."""

    async def on_setup_complete(self) -> tuple[bool, str]:
        """Verify everything is set up. Return (success, message)."""


