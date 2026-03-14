"""Loader for discovery, lifecycle, and protocol wiring."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from core.events import EventBus
from core.events.models import Event

if TYPE_CHECKING:
    from core.agents.registry import AgentRegistry
from core.extensions.context import ExtensionContext
from core.extensions.contract import (
    AgentProvider,
    ChannelProvider,
    ContextProvider,
    Extension,
    ExtensionState,
    SchedulerProvider,
    ServiceProvider,
    SetupProvider,
    ToolProvider,
    TurnContext,
)
from core.extensions.loader.context_builder import ExtensionContextBuilder
from core.extensions.loader.dependency_resolver import DependencyResolver
from core.extensions.loader.extension_factory import ExtensionFactory
from core.extensions.loader.health_check import HealthCheckManager
from core.extensions.loader.lifecycle import ExtensionStateMachine, TaskSupervisor
from core.extensions.loader.manifest_repository import ManifestRepository
from core.extensions.manifest import ExtensionManifest
from core.extensions.manifest_utils import iter_active_manifests
from core.extensions.routing.builtin_context import ActiveChannelContextProvider
from core.extensions.routing.event_wiring import EventWiringManager
from core.extensions.routing.project_context import ProjectInstructionsContextProvider
from core.extensions.routing.router import MessageRouter
from core.extensions.routing.scheduler_manager import SchedulerManager
from core.extensions.tool_resolver import ToolResolver
from core.llm import ModelRouterProtocol
from core.settings import get_setting

logger = logging.getLogger(__name__)


class Loader:
    """Extension lifecycle orchestration."""

    def __init__(
        self,
        extensions_dir: Path,
        data_dir: Path,
        settings: dict[str, Any],
    ) -> None:
        self._extensions_dir = extensions_dir
        self._data_dir = data_dir
        self._settings = settings
        self._manifest_repo = ManifestRepository(extensions_dir)
        self._dependency_resolver = DependencyResolver()
        self._extension_factory = ExtensionFactory(extensions_dir)
        self._router: MessageRouter | None = None
        self._model_router: ModelRouterProtocol | None = None
        self._manifests: list[ExtensionManifest] = []
        self._extensions: dict[str, Extension] = {}
        self._state: dict[str, ExtensionState] = {}
        self._tool_providers: list[ToolProvider] = []
        self._agent_registry: AgentRegistry | None = None
        self._service_tasks: dict[str, asyncio.Task[Any]] = {}
        self._service_task_names: dict[str, str] = {}
        self._task_supervisor = TaskSupervisor()
        self._health_manager = HealthCheckManager(self._extensions, self._state)
        self._scheduler_manager: SchedulerManager | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._event_bus: EventBus | None = None
        self._setup_providers: dict[str, bool] = {}

    def _lifecycle(self) -> ExtensionStateMachine:
        """State machine bound to current state dict (tests may replace self._state)."""
        return ExtensionStateMachine(self._state)

    def set_shutdown_event(self, event: asyncio.Event) -> None:
        self._shutdown_event = event

    def set_model_router(self, model_router: ModelRouterProtocol | None) -> None:
        """Inject ModelRouter for agent model resolution (core/llm)."""
        self._model_router = model_router

    def set_event_bus(self, event_bus: EventBus) -> None:
        """Inject EventBus for durable event flows."""
        self._event_bus = event_bus

    def set_agent_registry(self, registry: "AgentRegistry") -> None:
        """Inject AgentRegistry for agent discovery and delegation."""
        self._agent_registry = registry

    async def discover(self) -> None:
        """Scan extensions_dir for manifest.yaml; load and filter enabled."""
        self._manifests = []
        try:
            self._manifests = await self._manifest_repo.discover()
        except Exception as e:
            logger.exception("Manifest discovery failed: %s", e)

    def _resolve_dependency_order(self) -> list[ExtensionManifest]:
        """Topological sort by depends_on. Raises on cycle or missing dep."""
        return self._dependency_resolver.resolve(self._manifests)

    async def load_all(self) -> None:
        """Load extensions in dependency order; cascade failure to dependents."""
        order = self._resolve_dependency_order()
        self._extensions.clear()
        self._state.clear()
        failed_ids: set[str] = set()
        for manifest in order:
            failed_deps = [d for d in manifest.depends_on if d in failed_ids]
            if failed_deps:
                logger.error(
                    "Extension %s skipped: depends on failed %s",
                    manifest.id,
                    failed_deps,
                )
                self._lifecycle().mark_error(manifest.id)
                failed_ids.add(manifest.id)
                continue
            try:
                ext = self._load_one(manifest)
                self._extensions[manifest.id] = ext
                self._state[manifest.id] = ExtensionState.INACTIVE
            except Exception as e:
                logger.exception("Failed to load extension %s: %s", manifest.id, e)
                self._lifecycle().mark_error(manifest.id)
                failed_ids.add(manifest.id)

    def _load_one(self, manifest: ExtensionManifest) -> Extension:
        """Dynamic import or declarative adapter. Declarative agents need no main.py."""
        return self._extension_factory.create(manifest)

    def _get_manifest(self, ext_id: str) -> ExtensionManifest | None:
        """Return manifest for extension id, or None if not found."""
        return next((m for m in self._manifests if m.id == ext_id), None)

    def _check_deps_healthy(self, ext_id: str) -> list[str]:
        """Return depends_on entries in ERROR state (empty = all OK)."""
        manifest = self._get_manifest(ext_id)
        if not manifest:
            return []
        return [
            dep
            for dep in manifest.depends_on
            if self._state.get(dep) == ExtensionState.ERROR
        ]

    def _make_get_extension(self, caller_ext_id: str) -> Callable[[str], Any]:
        """Return a get_extension callable that enforces depends_on for the caller."""
        caller_manifest = self._get_manifest(caller_ext_id)
        if caller_manifest is None:
            raise ValueError(f"Extension '{caller_ext_id}' not found in manifests")
        allowed = set(caller_manifest.depends_on)

        def get_extension(ext_id: str) -> Any:
            if ext_id not in allowed:
                raise ValueError(
                    f"Extension '{caller_ext_id}' cannot access '{ext_id}': "
                    f"not in depends_on ({allowed})"
                )
            ext = self._extensions.get(ext_id)
            if ext is None:
                raise RuntimeError(
                    f"Dependency '{ext_id}' of '{caller_ext_id}' is not loaded"
                )
            if self._state.get(ext_id) == ExtensionState.ERROR:
                raise RuntimeError(
                    f"Dependency '{ext_id}' of '{caller_ext_id}' is in ERROR state"
                )
            return ext

        return get_extension

    def _get_restart_file_path(self) -> Path:
        """Restart flag file path from `supervisor.restart_file`."""
        restart_file = cast(
            str,
            get_setting(
                self._settings,
                "supervisor.restart_file",
                "sandbox/.restart_requested",
            ),
        )
        return self._data_dir.parent.parent / restart_file

    def resolve_tools(
        self, tool_ids: list[str], agent_id: str | None = None
    ) -> list[Any]:
        """Resolve tool IDs to actual tools from ToolProvider extensions or core_tools.

        Called by Loader for manifests and by AgentFactory for dynamic agents.
        agent_id is used for core_tools (e.g. restart_file_path, model resolution).
        """
        return ToolResolver(
            extensions=self._extensions,
            model_router=self._model_router,
            restart_file_path=self._get_restart_file_path(),
        ).resolve_tools(tool_ids, agent_id)

    def _register_agent_config_from_manifests(self) -> None:
        """Register agent configs from manifests with model_router."""
        if not self._model_router:
            return
        default_provider = self._model_router.get_default_provider()
        for manifest in self._manifests:
            if manifest.agent and default_provider:
                agent_id = manifest.agent_id or manifest.id
                if manifest.agent_config and agent_id in manifest.agent_config:
                    continue
                self._model_router.register_agent_config(
                    agent_id,
                    {"provider": default_provider, "model": manifest.agent.model},
                )
        for manifest in self._manifests:
            if manifest.agent_config:
                for aid, acfg in manifest.agent_config.items():
                    if isinstance(acfg, dict):
                        self._model_router.register_agent_config(aid, acfg)

    def _build_extension_context(
        self, ext_id: str, manifest: ExtensionManifest, router: MessageRouter
    ) -> ExtensionContext:
        """Build ExtensionContext for one extension."""
        return ExtensionContextBuilder(
            extensions_dir=self._extensions_dir,
            data_dir=self._data_dir,
            settings=self._settings,
            model_router=self._model_router,
            shutdown_event=self._shutdown_event,
            event_bus=self._event_bus,
            agent_registry=self._agent_registry,
            restart_file_path=self._get_restart_file_path(),
            resolve_tools=self.resolve_tools,
            get_extension_for=self._make_get_extension,
        ).build(
            ext_id,
            manifest,
            router,
            router.thread_manager,
            router.project_service,
        )

    async def initialize_all(self, router: MessageRouter) -> None:
        """Create context per extension, call initialize(ctx). Cascade dep failure."""
        self._router = router
        self._register_agent_config_from_manifests()
        for ext_id, ext in list(self._extensions.items()):
            if self._state.get(ext_id) != ExtensionState.INACTIVE:
                continue
            failed_deps = self._check_deps_healthy(ext_id)
            if failed_deps:
                logger.error(
                    "Extension %s skipped: depends on failed %s",
                    ext_id,
                    failed_deps,
                )
                self._lifecycle().mark_error(ext_id)
                continue
            manifest = self._get_manifest(ext_id)
            if not manifest:
                continue
            ctx = self._build_extension_context(ext_id, manifest, router)
            try:
                await ext.initialize(ctx)
            except Exception as e:
                logger.exception("initialize failed for %s: %s", ext_id, e)
                self._lifecycle().mark_error(ext_id)

    def _make_event_wiring_manager(self) -> EventWiringManager:
        """Construct an EventWiringManager from current Loader state."""
        return EventWiringManager(
            router=self._router,
            manifests=self._manifests,
            state=self._state,
            extensions=self._extensions,
            agent_registry=self._agent_registry,
        )

    async def _on_agent_task(self, event: Event) -> None:
        """Pass-through for test compatibility. Delegates to EventWiringManager."""
        if not self._router:
            return
        await self._make_event_wiring_manager()._on_agent_task(event)

    def wire_event_subscriptions(self, event_bus: EventBus) -> None:
        """Wire manifest-driven notify_user and invoke_agent handlers."""
        if not self._router:
            return
        self._make_event_wiring_manager().wire(event_bus)

    def _collect_context_providers(
        self, router: MessageRouter
    ) -> list[ContextProvider]:
        """Collect active ContextProviders plus built-in providers."""
        providers: list[ContextProvider] = [
            ActiveChannelContextProvider(router),
        ]
        if router.project_service is not None:
            providers.append(ProjectInstructionsContextProvider(router.project_service))
        ext_providers = [
            ext
            for ext_id, ext in self._extensions.items()
            if isinstance(ext, ContextProvider)
            and self._state.get(ext_id, ExtensionState.INACTIVE)
            == ExtensionState.ACTIVE
        ]
        providers.extend(ext_providers)
        return sorted(providers, key=lambda p: p.context_priority)

    def wire_context_providers(self, router: MessageRouter) -> None:
        """Wire ContextProvider chain into router's invoke middleware.

        The middleware returns system-role context, not an enriched user prompt.
        """
        providers = self._collect_context_providers(router)
        if not providers:
            return

        async def _middleware(prompt: str, turn_context: TurnContext) -> str:
            """Return context to inject into the system role."""
            parts: list[str] = []
            for provider in providers:
                ctx = await provider.get_context(prompt, turn_context)
                if ctx:
                    parts.append(ctx)
            if not parts:
                return ""
            return "\n\n---\n\n".join(parts)

        router.set_invoke_middleware(_middleware)

    def _register_static_agent(
        self,
        ext_id: str,
        ext: AgentProvider,
        manifest: ExtensionManifest,
    ) -> None:
        """Register AgentProvider with agent_registry. No-op if registry unavailable."""
        if not self._agent_registry:
            return
        from core.agents.registry import AgentRecord

        descriptor = ext.get_agent_descriptor()
        record = AgentRecord(
            id=ext_id,
            name=descriptor.name,
            description=descriptor.description,
            model=manifest.agent.model if manifest.agent else None,
            integration_mode=descriptor.integration_mode,
            tools=manifest.agent.uses_tools if manifest.agent else [],
            limits=manifest.agent.limits if manifest.agent else None,
            source="static",
        )
        self._agent_registry.register(record, ext)

    async def _update_setup_providers_state(self) -> None:
        """Call on_setup_complete for each SetupProvider; store configured state."""
        self._setup_providers.clear()
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            if not isinstance(ext, SetupProvider):
                continue
            try:
                ok, _msg = await ext.on_setup_complete()
                self._setup_providers[ext_id] = ok
            except Exception as e:
                logger.warning(
                    "SetupProvider %s on_setup_complete failed: %s", ext_id, e
                )
                self._setup_providers[ext_id] = False

    def detect_and_wire_all(self, router: MessageRouter) -> None:
        """Detect protocols via isinstance; wire ToolProvider, ChannelProvider, etc."""
        self._tool_providers = []
        if self._agent_registry:
            self._agent_registry.clear()
        self._scheduler_manager = SchedulerManager(state=self._state, router=router)
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            manifest = self._get_manifest(ext_id)
            if isinstance(ext, ToolProvider):
                self._tool_providers.append(ext)
            if isinstance(ext, AgentProvider) and manifest:
                self._register_static_agent(ext_id, ext, manifest)
            if isinstance(ext, ChannelProvider):
                router.register_channel(ext_id, ext)
            if isinstance(ext, SchedulerProvider) and manifest:
                self._scheduler_manager.register(ext_id, ext, manifest)

        channel_ids = {
            eid for eid, e in self._extensions.items() if isinstance(e, ChannelProvider)
        }
        channel_descriptions = {
            m.id: m.name for m in self._manifests if m.id in channel_ids
        }
        router.set_channel_descriptions(channel_descriptions)

    async def start_all(self) -> None:
        """Call `start()` on all extensions and boot background services."""
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.INACTIVE:
                continue
            failed_deps = self._check_deps_healthy(ext_id)
            if failed_deps:
                logger.error(
                    "Extension %s skipped: depends on failed %s",
                    ext_id,
                    failed_deps,
                )
                self._lifecycle().mark_error(ext_id)
                continue
            try:
                await ext.start()
                self._lifecycle().mark_active(ext_id)
                if isinstance(ext, ServiceProvider):
                    task_name = f"service::{ext_id}"
                    self._service_task_names[ext_id] = task_name

                    async def _on_error(
                        _task_name: str,
                        exc: BaseException,
                        _ext_id: str = ext_id,
                        _ext: Extension = ext,
                    ) -> None:
                        logger.exception(
                            "Service task failed for %s: %s",
                            _ext_id,
                            exc,
                        )
                        self._lifecycle().mark_error(_ext_id)
                        try:
                            await _ext.stop()
                        except Exception as stop_error:
                            logger.exception(
                                "Service stop failed for %s: %s",
                                _ext_id,
                                stop_error,
                            )
                        self._service_tasks.pop(_ext_id, None)
                        self._service_task_names.pop(_ext_id, None)

                    self._service_tasks[ext_id] = self._task_supervisor.start(
                        task_name,
                        ext.run_background,
                        on_error=_on_error,
                    )
            except Exception as e:
                logger.exception("start failed for %s: %s", ext_id, e)
                self._lifecycle().mark_error(ext_id)
        if self._scheduler_manager:
            self._scheduler_manager.start()
        self._health_manager.start()

    def get_mcp_servers(self) -> list[Any]:
        """Collect MCP server instances from ACTIVE extensions."""
        servers: list[Any] = []
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.ACTIVE:
                continue
            if not hasattr(ext, "get_mcp_servers") or not callable(ext.get_mcp_servers):
                continue
            try:
                result = ext.get_mcp_servers()
                if result:
                    servers.extend(result if isinstance(result, list) else list(result))
            except Exception as e:
                logger.exception("get_mcp_servers failed for %s: %s", ext_id, e)
        return servers

    def get_extensions(self) -> dict[str, Any]:
        """Return extensions dict for tools that need to resolve SetupProvider."""
        return self._extensions

    def get_available_tool_ids(self) -> list[str]:
        """Return tool IDs usable in agent uses_tools or create_agent.

        Includes 'core_tools' plus all ToolProvider extension IDs.
        """
        ids = ["core_tools"]
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            if isinstance(ext, ToolProvider):
                ids.append(ext_id)
        return sorted(set(ids))

    def get_all_tools(self) -> list[Any]:
        """Collect tools from all ToolProvider extensions."""
        tools: list[Any] = []
        for ext in self._tool_providers:
            try:
                tools.extend(ext.get_tools())
            except Exception as e:
                logger.exception("get_tools failed: %s", e)
        return tools

    def _collect_tool_agent_parts(self) -> list[str]:
        """Return tool descriptions for capabilities summary. Agents excluded."""
        tool_parts: list[str] = []
        for ext_id, manifest in iter_active_manifests(self._manifests, self._state):
            if ext_id not in self._extensions:
                continue
            if not manifest.description:
                continue
            is_agent = (
                self._agent_registry is not None
                and self._agent_registry.get(ext_id) is not None
            )
            if is_agent:
                continue
            tool_parts.append(f"- {ext_id}: {manifest.description.strip()}")
        return tool_parts

    def _collect_setup_sections(self) -> list[str]:
        """Return setup_instructions for unconfigured SetupProvider extensions."""
        parts: list[str] = []
        for ext_id, is_configured in self._setup_providers.items():
            if is_configured:
                continue
            manifest = self._get_manifest(ext_id)
            if not manifest or not manifest.setup_instructions.strip():
                continue
            parts.append(
                f"- {ext_id}: {manifest.setup_instructions.strip()}"
            )
        return parts

    def _collect_mcp_aliases(self) -> list[str]:
        """Collect MCP server aliases from ACTIVE extensions."""
        mcp_aliases: list[str] = []
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.ACTIVE:
                continue
            if not hasattr(ext, "get_mcp_server_aliases") or not callable(
                ext.get_mcp_server_aliases
            ):
                continue
            try:
                aliases = ext.get_mcp_server_aliases()
                if aliases:
                    mcp_aliases.extend(
                        aliases if isinstance(aliases, list) else list(aliases)
                    )
            except Exception as e:
                logger.exception("get_mcp_server_aliases failed for %s: %s", ext_id, e)
        return mcp_aliases

    def get_capabilities_summary(self) -> str:
        """Build a natural-language capability summary for the orchestrator."""
        tool_parts = self._collect_tool_agent_parts()
        setup_parts = self._collect_setup_sections()
        mcp_aliases = self._collect_mcp_aliases()
        sections: list[str] = []
        if setup_parts:
            sections.append(
                "Extensions needing setup:\n" + "\n".join(setup_parts)
            )
        if tool_parts:
            sections.append("Available tools:\n" + "\n".join(tool_parts))
        if self._agent_registry and self._agent_registry.list_agents():
            sections.append(
                "Agent delegation:\n"
                "Use list_agents to discover available specialized agents.\n"
                "Use delegate_task to assign work to an agent."
            )
        if mcp_aliases:
            mcp_lines = "\n".join(f"- {a}" for a in mcp_aliases)
            sections.append("MCP servers:\n" + mcp_lines)
        return "\n\n".join(sections) if sections else "No extensions loaded."

    async def shutdown(self) -> None:
        """Stop then destroy all extensions in reverse order."""
        await self._health_manager.stop()
        if self._scheduler_manager:
            await self._scheduler_manager.stop()
        for ext_id, task_name in list(self._service_task_names.items()):
            await self._task_supervisor.stop(task_name)
            self._service_tasks.pop(ext_id, None)
            self._service_task_names.pop(ext_id, None)
        order = self._resolve_dependency_order()
        for manifest in reversed(order):
            ext_id = manifest.id
            ext = self._extensions.get(ext_id)
            if not ext:
                continue
            try:
                await ext.stop()
            except Exception as e:
                logger.exception("stop failed for %s: %s", ext_id, e)
            try:
                await ext.destroy()
            except Exception as e:
                logger.exception("destroy failed for %s: %s", ext_id, e)
