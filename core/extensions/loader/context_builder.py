"""ExtensionContextBuilder: construct ExtensionContext for one extension."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.extensions.context import ExtensionContext
from core.extensions.instructions import resolve_instructions
from core.extensions.manifest import ExtensionManifest
from core.extensions.persistence.project_service import ProjectService
from core.extensions.persistence.thread_manager import ThreadManager
from core.extensions.routing.router import MessageRouter
from core.llm import ModelRouterProtocol

if TYPE_CHECKING:
    from core.agents.registry import AgentRegistry
    from core.events.bus import EventBus


class ExtensionContextBuilder:
    """Builds ExtensionContext with resolved config, tools, and instructions."""

    def __init__(
        self,
        extensions_dir: Path,
        data_dir: Path,
        settings: dict[str, Any],
        model_router: ModelRouterProtocol | None,
        shutdown_event: Any,
        event_bus: "EventBus | None",
        agent_registry: "AgentRegistry | None",
        restart_file_path: Path,
        resolve_tools: Callable[[list[str], str | None], list[Any]],
        get_extension_for: Callable[[str], Callable[[str], Any]],
    ) -> None:
        self._extensions_dir = extensions_dir
        self._data_dir = data_dir
        self._settings = settings
        self._model_router = model_router
        self._shutdown_event = shutdown_event
        self._event_bus = event_bus
        self._agent_registry = agent_registry
        self._restart_file_path = restart_file_path
        self._resolve_tools = resolve_tools
        self._get_extension_for = get_extension_for

    def build(
        self,
        ext_id: str,
        manifest: ExtensionManifest,
        router: MessageRouter,
        thread_manager: ThreadManager,
        project_service: ProjectService | None,
    ) -> ExtensionContext:
        """Create ExtensionContext for ext_id and manifest."""
        data_dir_path = self._data_dir / ext_id
        overrides = self._settings.get("extensions", {}).get(ext_id, {}) or {}
        config = {**manifest.config, **overrides}
        resolved_tools = self._resolve_agent_tools(manifest)
        resolved_instructions = self._resolve_agent_instructions(manifest, ext_id)
        agent_model = manifest.agent.model if manifest.agent else ""
        agent_id = getattr(manifest, "agent_id", None) or (
            ext_id if manifest.agent else None
        )
        return ExtensionContext(
            extension_id=ext_id,
            config=config,
            logger=logging.getLogger(f"ext.{ext_id}"),
            router=router,
            thread_manager=thread_manager,
            project_service=project_service,
            get_extension=self._get_extension_for(ext_id),
            data_dir_path=data_dir_path,
            shutdown_event=self._shutdown_event,
            resolved_tools=resolved_tools,
            resolved_instructions=resolved_instructions,
            agent_model=agent_model,
            model_router=self._model_router,
            agent_id=agent_id,
            event_bus=self._event_bus,
            agent_registry=self._agent_registry,
            restart_file_path=self._restart_file_path,
        )

    def _resolve_agent_tools(self, manifest: ExtensionManifest) -> list[Any]:
        if not manifest.agent:
            return []
        agent_id = getattr(manifest, "agent_id", None) or manifest.id
        return self._resolve_tools(manifest.agent.uses_tools, agent_id)

    def _resolve_agent_instructions(
        self, manifest: ExtensionManifest, ext_id: str
    ) -> str:
        if not manifest.agent:
            return ""
        extension_dir = self._extensions_dir / ext_id
        instructions_file = ""
        if (extension_dir / "prompt.jinja2").exists():
            instructions_file = "prompt.jinja2"
        return resolve_instructions(
            instructions=manifest.agent.instructions,
            instructions_file=instructions_file,
            extension_dir=extension_dir,
            template_vars={"sandbox_dir": str(self._extensions_dir.parent)},
        )

