"""ExtensionContext: kernel API for extensions."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.events.topics import SystemTopics
from core.extensions.contract import TurnContext
from core.extensions.persistence.models import ProjectInfo, ThreadInfo
from core.extensions.persistence.project_service import ProjectService
from core.extensions.persistence.thread_manager import ThreadManager
from core.extensions.routing.router import MessageRouter
from core.extensions.update_fields import UNSET, UnsetType
from core.llm import ModelRouterProtocol
from core.logging_config import SubsystemLogger
from core.secrets import get_secret_async, set_secret_async

if TYPE_CHECKING:
    from core.agents.registry import AgentRegistry
    from core.events.bus import EventBus
    from core.events.models import Event


class ExtensionContext:
    """Everything an extension can do — only through this object."""

    def __init__(
        self,
        extension_id: str,
        config: dict[str, Any],
        logger: SubsystemLogger,
        router: MessageRouter,
        thread_manager: ThreadManager,
        project_service: ProjectService | None,
        get_extension: Callable[[str], Any],
        data_dir_path: Path,
        shutdown_event: asyncio.Event | None,
        resolved_tools: list[Any] | None = None,
        resolved_instructions: str = "",
        agent_model: str = "",
        model_router: ModelRouterProtocol | None = None,
        agent_id: str | None = None,
        event_bus: "EventBus | None" = None,
        agent_registry: "AgentRegistry | None" = None,
        restart_file_path: Path | None = None,
    ) -> None:
        self.extension_id = extension_id
        self.config = config
        self.logger = logger
        self._router = router
        self._threads = thread_manager
        self._projects = project_service
        self._get_extension = get_extension
        self._data_dir_path = data_dir_path
        self._shutdown_event = shutdown_event
        self.resolved_tools: list[Any] = resolved_tools or []
        self.resolved_instructions: str = resolved_instructions
        self.agent_model: str = agent_model
        self._model_router = model_router
        self.agent_id: str | None = agent_id or extension_id
        self._event_bus = event_bus
        self._agent_registry = agent_registry
        self._restart_file_path = restart_file_path
        self.on_user_message = self._router.handle_user_message

    @property
    def model_router(self) -> ModelRouterProtocol | None:
        """ModelRouter for get_model(agent_id). None if not set (e.g. legacy runner)."""
        return self._model_router

    @property
    def agent_registry(self) -> "AgentRegistry | None":
        """AgentRegistry for agent discovery and delegation. None if not set."""
        return self._agent_registry

    async def notify_user(self, text: str, channel_id: str | None = None) -> None:
        """Send notification to user via system.user.notify. Guaranteed delivery."""
        if self._event_bus:
            await self._event_bus.publish(
                SystemTopics.USER_NOTIFY,
                self.extension_id,
                {"text": text, "channel_id": channel_id},
            )
        else:
            await self._router.notify_user(text, channel_id)

    async def invoke_agent(self, prompt: str) -> str:
        """Ask the agent to process a prompt and return a response."""
        return await self._router.invoke_agent(prompt)

    async def invoke_agent_streamed(
        self,
        prompt: str,
        on_chunk: Callable[[str], Awaitable[None]],
        on_tool_call: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Ask the agent to process a prompt with streaming callbacks."""
        return await self._router.invoke_agent_streamed(
            prompt,
            on_chunk=on_chunk,
            on_tool_call=on_tool_call,
        )

    async def invoke_agent_background(self, prompt: str) -> str:
        """Run the Orchestrator in background mode and return its response."""
        return await self._router.invoke_agent_background(prompt)

    async def enrich_prompt(
        self,
        prompt: str,
        turn_context: "TurnContext | None" = None,
    ) -> str:
        """Apply full ContextProvider chain (memory, etc.) without running the agent."""
        return await self._router.enrich_prompt(prompt, turn_context)

    def subscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Subscribe to an internal event (e.g. user_message, agent_response)."""
        self._router.subscribe(event, handler)

    def unsubscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Remove a previously registered subscription."""
        self._router.unsubscribe(event, handler)

    async def emit(
        self,
        topic: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> None:
        """Publish event to the Event Bus. Fire-and-forget."""
        if self._event_bus:
            await self._event_bus.publish(
                topic, self.extension_id, payload, correlation_id
            )

    async def request_agent_task(
        self, prompt: str, channel_id: str | None = None
    ) -> None:
        """Ask the Orchestrator to handle a task. Response goes to user."""
        await self.emit(
            SystemTopics.AGENT_TASK,
            {"prompt": prompt, "channel_id": channel_id},
        )

    async def request_agent_background(
        self, prompt: str, correlation_id: str | None = None
    ) -> None:
        """Trigger the Orchestrator silently. No user response."""
        await self.emit(
            SystemTopics.AGENT_BACKGROUND,
            {"prompt": prompt, "correlation_id": correlation_id},
        )

    async def purge_scheduled_events(
        self,
        schedule_id: int,
        schedule_type: str,
    ) -> int:
        """Purge queued event_journal items linked to a cancelled schedule."""
        if not self._event_bus:
            return 0
        return await self._event_bus.purge_scheduled_events(
            schedule_id=schedule_id,
            schedule_type=schedule_type,
        )

    def subscribe_event(
        self,
        topic: str,
        handler: Callable[["Event"], Awaitable[None]],
    ) -> None:
        """Subscribe to durable events via the Event Bus."""
        if self._event_bus:
            self._event_bus.subscribe(topic, handler, self.extension_id)

    async def get_secret(self, name: str) -> str | None:
        """Get a secret by name (keyring or os.environ fallback)."""
        return await get_secret_async(name)

    async def set_secret(self, name: str, value: str) -> None:
        """Store a secret in the OS keyring for secure input flows."""
        await set_secret_async(name, value)

    def get_config(self, key: str, default: Any = None) -> Any:
        """Read a value from the config: block in manifest.yaml."""
        return self.config.get(key, default)

    def get_extension(self, extension_id: str) -> Any:
        """Get an instance of another extension (only from depends_on)."""
        return self._get_extension(extension_id)

    async def list_threads(
        self,
        include_archived: bool = False,
        project_id: str | None = None,
        channel_id: str | None = None,
    ) -> list[ThreadInfo]:
        """List persisted thread metadata from thread.db."""
        return await self._threads.list_threads(
            include_archived=include_archived,
            project_id=project_id,
            channel_id=channel_id,
        )

    async def create_thread(
        self,
        *,
        thread_id: str,
        channel_id: str,
        project_id: str | None = None,
        title: str | None = None,
    ) -> ThreadInfo:
        """Create a persisted thread row before any messages are sent."""
        if project_id is not None and self._projects is not None:
            project = await asyncio.to_thread(self._projects.get_project, project_id)
            if project is None:
                raise ValueError(f"Project {project_id} not found")
        return await asyncio.to_thread(
            self._threads.thread_repository.create_thread,
            thread_id,
            channel_id,
            project_id,
            title,
            int(time.time()),
        )

    async def get_thread(
        self, thread_id: str, include_archived: bool = False
    ) -> ThreadInfo | None:
        """Read persisted thread metadata."""
        return await self._threads.get_thread(
            thread_id, include_archived=include_archived
        )

    async def update_thread(
        self,
        thread_id: str,
        *,
        title: str | None | UnsetType = UNSET,
        project_id: str | None | UnsetType = UNSET,
        is_archived: bool | UnsetType = UNSET,
    ) -> ThreadInfo | None:
        """Update selected persisted thread metadata fields."""
        if (
            project_id is not UNSET
            and project_id is not None
            and self._projects is not None
        ):
            project = await asyncio.to_thread(self._projects.get_project, project_id)
            if project is None:
                raise ValueError(f"Project {project_id} not found")
        return await self._threads.update_thread(
            thread_id,
            title=title,
            project_id=project_id,
            is_archived=is_archived,
        )

    async def archive_thread(self, thread_id: str) -> bool:
        """Soft-archive a thread without deleting its history."""
        return await self._threads.archive_thread(thread_id)

    async def get_thread_history(self, thread_id: str) -> list[dict[str, Any]] | None:
        """Return stored messages/items for a thread. None if thread is unknown."""
        return await self._threads.get_thread_history(thread_id)

    async def list_projects(self) -> list[ProjectInfo]:
        """List persisted projects."""
        if self._projects is None:
            return []
        return await asyncio.to_thread(self._projects.list_projects)

    async def get_project(self, project_id: str) -> ProjectInfo | None:
        """Read one persisted project."""
        if self._projects is None:
            return None
        return await asyncio.to_thread(self._projects.get_project, project_id)

    async def create_project(
        self,
        *,
        name: str,
        description: str | None = None,
        icon: str | None = None,
        instructions: str | None = None,
        agent_config: dict[str, Any] | None = None,
        files: list[str] | None = None,
        links: list[str] | None = None,
    ) -> ProjectInfo:
        """Create a project in thread.db."""
        if self._projects is None:
            raise RuntimeError("Project service is not configured")
        return await asyncio.to_thread(
            self._projects.create_project,
            name=name,
            description=description,
            icon=icon,
            instructions=instructions,
            agent_config=agent_config,
            files=files or [],
            links=links or [],
            now_ts=int(time.time()),
        )

    async def update_project(
        self,
        project_id: str,
        *,
        name: str | UnsetType = UNSET,
        description: str | None | UnsetType = UNSET,
        icon: str | None | UnsetType = UNSET,
        instructions: str | None | UnsetType = UNSET,
        agent_config: dict[str, Any] | None | UnsetType = UNSET,
        files: list[str] | UnsetType = UNSET,
        links: list[str] | UnsetType = UNSET,
    ) -> ProjectInfo | None:
        """Update selected project metadata fields."""
        if self._projects is None:
            raise RuntimeError("Project service is not configured")
        return await asyncio.to_thread(
            self._projects.update_project,
            project_id,
            name=name,
            description=description,
            icon=icon,
            instructions=instructions,
            agent_config=agent_config,
            files=files,
            links=links,
            now_ts=int(time.time()),
        )

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project and unlink bound threads via foreign key rules."""
        if self._projects is None:
            return False
        return await asyncio.to_thread(self._projects.delete_project, project_id)

    @property
    def data_dir(self) -> Path:
        """Private extension folder: sandbox/data/<extension_id>/."""
        self._data_dir_path.mkdir(parents=True, exist_ok=True)
        return self._data_dir_path

    def request_restart(self) -> None:
        """Ask supervisor to restart the kernel.

        When running under the Loader, the path is taken from supervisor.restart_file
        (injected at construction). Otherwise falls back to sandbox/.restart_requested.
        """
        restart_file = (
            self._restart_file_path
            if self._restart_file_path is not None
            else self._data_dir_path.parent.parent / ".restart_requested"
        )
        restart_file.parent.mkdir(parents=True, exist_ok=True)
        restart_file.write_text("restart requested", encoding="utf-8")

    def request_shutdown(self) -> None:
        """Shut down the application."""
        if self._shutdown_event:
            self._shutdown_event.set()
