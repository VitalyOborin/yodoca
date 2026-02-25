"""Extension protocols: base contract and capability interfaces.

Loader detects capabilities via isinstance(ext, Protocol). No type field in manifest.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


@runtime_checkable
class Extension(Protocol):
    """Base contract: lifecycle. Identity (id, name, version) comes from manifest."""

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
        """Reactive: reply to a specific user who sent a message."""

    async def send_message(self, message: str) -> None:
        """Proactive: deliver to the channel's default recipient.
        All addressing (user_id, chat_id, etc.) is internal to the channel."""


@runtime_checkable
class ServiceProvider(Protocol):
    """Runs a background service."""

    async def run_background(self) -> None:
        """Main service loop. Must handle CancelledError."""


@runtime_checkable
class StreamingChannelProvider(Protocol):
    """Channel that supports incremental response delivery."""

    async def on_stream_start(self, user_id: str) -> None: ...

    async def on_stream_chunk(self, user_id: str, chunk: str) -> None: ...

    async def on_stream_status(self, user_id: str, status: str) -> None: ...

    async def on_stream_end(self, user_id: str, full_text: str) -> None: ...


@runtime_checkable
class SchedulerProvider(Protocol):
    """Periodic tasks by schedules from manifest.yaml (schedules section).
    Loader reads schedules from manifest and calls execute_task(name) per cron trigger."""

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        """Execute task by name from manifest schedules[].task (or .name if task empty).
        Return {'text': '...'} to notify user, or None."""


@dataclass(frozen=True)
class TurnContext:
    agent_id: str | None = None
    channel_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None


@runtime_checkable
class ContextProvider(Protocol):
    """Extension that enriches agent context before each invocation.
    Called by the kernel before Runner.run() on every agent turn.
    Multiple ContextProviders coexist; kernel calls them in priority order.
    """

    @property
    def context_priority(self) -> int:
        """Lower value = earlier in chain. Default: 100."""
        return 100

    async def get_context(
        self,
        prompt: str,
        turn_context: TurnContext,
    ) -> str | None:
        """Return context string to prepend, or None/empty to skip."""

        ...


@runtime_checkable
class SetupProvider(Protocol):
    """Extension that needs configuration (secrets, settings)."""

    def get_setup_schema(self) -> list[dict]:
        """[{name, description, secret, required}] — list of setup parameters."""

    async def apply_config(self, name: str, value: str) -> None:
        """Save config value. Extension decides where to store it."""

    async def on_setup_complete(self) -> tuple[bool, str]:
        """Verify everything is set up. Return (success, message)."""


@dataclass(frozen=True)
class AgentResponse:
    """Structured result from AgentProvider.invoke()."""

    status: Literal["success", "error", "refused"]
    content: str
    error: str | None = None
    tokens_used: int | None = None
    turns_used: int | None = None


@dataclass(frozen=True)
class AgentInvocationContext:
    """Typed context passed to AgentProvider.invoke()."""

    conversation_summary: str | None = None
    user_message: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class AgentDescriptor:
    """Metadata for LLM routing and Loader wiring."""

    name: str
    description: str
    integration_mode: Literal["tool", "handoff"]


@runtime_checkable
class AgentProvider(Protocol):
    """Extension that provides a specialized AI agent."""

    def get_agent_descriptor(self) -> AgentDescriptor:
        """Return metadata for LLM routing."""

    async def invoke(
        self, task: str, context: AgentInvocationContext | None = None
    ) -> AgentResponse:
        """Execute a task and return structured result."""
