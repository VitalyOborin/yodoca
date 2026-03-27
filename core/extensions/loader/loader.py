"""Loader for discovery, lifecycle, and protocol wiring."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from core.events import EventBus
from core.events.models import Event
from core.extensions.context import ExtensionContext
from core.extensions.contract import (
    Extension,
    ExtensionState,
    ServiceProvider,
    ToolProvider,
)
from core.extensions.loader.capabilities_summary import CapabilitiesSummaryBuilder
from core.extensions.loader.context_builder import (
    ExtensionContextBuilder,
    merge_extension_config,
)
from core.extensions.loader.dependency_resolver import DependencyResolver
from core.extensions.loader.diagnostics import DiagnosticPhase
from core.extensions.loader.diagnostics_manager import DiagnosticsManager
from core.extensions.loader.extension_factory import ExtensionFactory
from core.extensions.loader.health_check import HealthCheckManager
from core.extensions.loader.lifecycle import ExtensionStateMachine, TaskSupervisor
from core.extensions.loader.manifest_repository import ManifestRepository
from core.extensions.loader.mcp_collector import McpCollector
from core.extensions.loader.protocol_wiring import ProtocolWiringManager
from core.extensions.manifest import ExtensionManifest
from core.extensions.routing.context_wiring import (
    wire_context_providers as wire_router_context_providers,
)
from core.extensions.routing.event_wiring import EventWiringManager
from core.extensions.routing.router import MessageRouter
from core.extensions.routing.scheduler_manager import SchedulerManager
from core.extensions.tool_resolver import ToolResolver
from core.llm import ModelRouterProtocol
from core.settings import format_validation_errors
from core.settings_models import AppSettings

if TYPE_CHECKING:
    from core.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)


class Loader:
    """Extension lifecycle orchestration."""

    def __init__(
        self,
        extensions_dir: Path,
        data_dir: Path,
        settings: AppSettings,
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
        self._manifest_index: dict[str, ExtensionManifest] = {}
        self._manifest_list_id: int | None = None
        self._extensions: dict[str, Extension] = {}
        self._state: dict[str, ExtensionState] = {}
        self._setup_providers: dict[str, bool] = {}
        self._diagnostics_manager = DiagnosticsManager()
        self._mcp_collector = McpCollector(self._extensions, self._state)
        self._tool_providers: list[ToolProvider] = []
        self._agent_registry: AgentRegistry | None = None
        self._capabilities_builder = CapabilitiesSummaryBuilder(
            self._state,
            self._extensions,
            settings,
            None,
            self._mcp_collector,
        )
        self._service_tasks: dict[str, asyncio.Task[Any]] = {}
        self._service_task_names: dict[str, str] = {}
        self._task_supervisor = TaskSupervisor()
        self._health_manager = HealthCheckManager(
            self._extensions,
            self._state,
            on_failure=self._diagnostics_manager.record_health_failure,
        )
        self._scheduler_manager: SchedulerManager | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._event_bus: EventBus | None = None

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
        self._diagnostics_manager.set_event_bus(event_bus)

    def set_agent_registry(self, registry: "AgentRegistry") -> None:
        """Inject AgentRegistry for agent discovery and delegation."""
        self._agent_registry = registry
        self._capabilities_builder = CapabilitiesSummaryBuilder(
            self._state,
            self._extensions,
            self._settings,
            registry,
            self._mcp_collector,
        )

    def _ensure_manifest_index(self) -> None:
        if self._manifest_list_id != id(self._manifests):
            self._manifest_index = {m.id: m for m in self._manifests}
            self._manifest_list_id = id(self._manifests)

    async def discover(self) -> None:
        """Scan extensions_dir for manifest.yaml; load and filter enabled."""
        self._manifests = []
        try:
            self._manifests = await self._manifest_repo.discover()
        except Exception as e:
            logger.exception("Manifest discovery failed: %s", e)
        self._ensure_manifest_index()

    def _resolve_dependency_order(self) -> list[ExtensionManifest]:
        """Topological sort by depends_on. Raises on cycle or missing dep."""
        return self._dependency_resolver.resolve(self._manifests)

    async def _skip_due_to_failed_deps(
        self,
        ext_id: str,
        failed_deps: list[str],
        phase: DiagnosticPhase,
    ) -> bool:
        """If deps failed, mark error, record diagnostic, return True to skip."""
        if not failed_deps:
            return False
        logger.error(
            "Extension %s skipped: depends on failed %s",
            ext_id,
            failed_deps,
        )
        self._lifecycle().mark_error(ext_id)
        await self._diagnostics_manager.record_diagnostic(
            ext_id,
            phase=phase,
            reason="dependency_failed",
            message=f"Skipped {phase}: depends on failed {failed_deps}",
            dependency_chain=failed_deps,
        )
        return True

    async def load_all(self) -> None:
        """Load extensions in dependency order; cascade failure to dependents."""
        order = self._resolve_dependency_order()
        self._extensions.clear()
        self._state.clear()
        self._diagnostics_manager.clear()
        failed_ids: set[str] = set()
        for manifest in order:
            failed_deps = [d for d in manifest.depends_on if d in failed_ids]
            if await self._skip_due_to_failed_deps(manifest.id, failed_deps, "load"):
                failed_ids.add(manifest.id)
                continue
            try:
                ext = self._load_one(manifest)
                self._extensions[manifest.id] = ext
                self._state[manifest.id] = ExtensionState.INACTIVE
            except Exception as e:
                logger.exception("Failed to load extension %s: %s", manifest.id, e)
                self._lifecycle().mark_error(manifest.id)
                await self._diagnostics_manager.record_diagnostic(
                    manifest.id,
                    phase="load",
                    reason="import_error",
                    message=str(e),
                    exception=e,
                )
                failed_ids.add(manifest.id)

    def _load_one(self, manifest: ExtensionManifest) -> Extension:
        """Dynamic import or declarative adapter. Declarative agents need no main.py."""
        return self._extension_factory.create(manifest)

    def _get_manifest(self, ext_id: str) -> ExtensionManifest | None:
        """Return manifest for extension id, or None if not found."""
        self._ensure_manifest_index()
        return self._manifest_index.get(ext_id)

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
        restart_file = self._settings.supervisor.restart_file
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
            extension_report_getter=self.get_extension_status_report,
        ).resolve_tools(tool_ids, agent_id)

    async def _validate_extension_config(
        self,
        ext_id: str,
        ext: Extension,
        manifest: ExtensionManifest,
    ) -> bool:
        """Validate merged config for one extension, recording per-extension failures."""
        model_cls = getattr(type(ext), "ConfigModel", None)
        if model_cls is None:
            return True
        merged = merge_extension_config(self._settings, ext_id, manifest)
        try:
            model_cls.model_validate(merged)
        except ValidationError as e:
            details = format_validation_errors(e, prefix=f"extensions.{ext_id}.")
            logger.error("Config validation failed for %s:\n%s", ext_id, details)
            self._lifecycle().mark_error(ext_id)
            await self._diagnostics_manager.record_diagnostic(
                ext_id,
                phase="config_validate",
                reason="config_invalid",
                message=details,
                exception=e,
            )
            return False
        return True

    def _register_agent_config_from_manifests(self) -> None:
        """Register agent configs from manifests with model_router."""
        if not self._model_router:
            return
        default_provider = self._model_router.get_default_provider()
        for manifest in self._manifests:
            if manifest.agent and default_provider and manifest.agent.model:
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
            manifest = self._get_manifest(ext_id)
            if not manifest:
                continue
            if not await self._validate_extension_config(ext_id, ext, manifest):
                continue
            failed_deps = self._check_deps_healthy(ext_id)
            if await self._skip_due_to_failed_deps(ext_id, failed_deps, "initialize"):
                continue
            ctx = self._build_extension_context(ext_id, manifest, router)
            try:
                await ext.initialize(ctx)
            except Exception as e:
                logger.exception("initialize failed for %s: %s", ext_id, e)
                self._lifecycle().mark_error(ext_id)
                await self._diagnostics_manager.record_diagnostic(
                    ext_id,
                    phase="initialize",
                    reason="init_error",
                    message=str(e),
                    exception=e,
                )

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

    def wire_context_providers(self, router: MessageRouter) -> None:
        """Wire ContextProvider chain into router's invoke middleware.

        The middleware returns system-role context, not an enriched user prompt.
        """
        wire_router_context_providers(
            router,
            self._extensions,
            self._state,
            self.get_capabilities_summary,
        )

    async def update_setup_providers_state(self) -> None:
        """Call on_setup_complete for each SetupProvider; store configured state."""
        mgr = ProtocolWiringManager(
            self._extensions,
            self._state,
            self._settings,
            self._agent_registry,
            self._get_manifest,
        )
        await mgr.update_setup_providers(self._setup_providers)

    def detect_and_wire_all(self, router: MessageRouter) -> None:
        """Detect protocols via isinstance; wire ToolProvider, ChannelProvider, etc."""
        mgr = ProtocolWiringManager(
            self._extensions,
            self._state,
            self._settings,
            self._agent_registry,
            self._get_manifest,
        )
        self._tool_providers, self._scheduler_manager = mgr.detect_and_wire(
            router, self._manifests
        )

    async def start_all(self) -> None:
        """Call `start()` on all extensions and boot background services."""
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.INACTIVE:
                continue
            failed_deps = self._check_deps_healthy(ext_id)
            if await self._skip_due_to_failed_deps(ext_id, failed_deps, "start"):
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
                        await self._diagnostics_manager.record_diagnostic(
                            _ext_id,
                            phase="start",
                            reason="start_error",
                            message=str(exc),
                            exception=exc,
                        )
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
                await self._diagnostics_manager.record_diagnostic(
                    ext_id,
                    phase="start",
                    reason="start_error",
                    message=str(e),
                    exception=e,
                )
        if self._scheduler_manager:
            self._scheduler_manager.start()
        self._health_manager.start()

    def get_mcp_servers(self) -> list[Any]:
        """Collect MCP server instances from ACTIVE extensions."""
        return self._mcp_collector.get_mcp_servers()

    def get_extensions(self) -> dict[str, Any]:
        """Return extensions dict for tools that need to resolve SetupProvider."""
        return self._extensions

    def get_extension_diagnostic(
        self, ext_id: str, latest_only: bool = True
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Return latest or full diagnostic history for one extension."""
        return self._diagnostics_manager.get_extension_diagnostic(
            ext_id, latest_only=latest_only
        )

    def get_failed_extensions(self) -> dict[str, dict[str, Any]]:
        """Return latest diagnostic for extensions currently in ERROR state."""
        return self._diagnostics_manager.get_failed_extensions(self._state)

    def get_extension_status_report(self) -> dict[str, Any]:
        """Return machine-readable status and diagnostics for all discovered extensions."""
        return self._diagnostics_manager.get_extension_status_report(
            self._manifests, self._state
        )

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

    def get_tool_catalog(self) -> dict[str, dict[str, Any]]:
        """Return tool metadata for create_agent/list_available_tools.

        Format:
        {
            "tool_id": {
                "description": str,
            },
            ...
        }
        """
        catalog: dict[str, dict[str, Any]] = {
            "core_tools": {
                "description": "Built-in core tools (file, apply_patch, restart).",
            }
        }
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            if not isinstance(ext, ToolProvider):
                continue
            manifest = self._get_manifest(ext_id)
            description = ""
            if manifest:
                description = manifest.description.strip()
            catalog[ext_id] = {
                "description": description,
            }
        return catalog

    def get_all_tools(self) -> list[Any]:
        """Collect tools from all ToolProvider extensions."""
        tools: list[Any] = []
        for ext in self._tool_providers:
            try:
                tools.extend(ext.get_tools())
            except Exception as e:
                logger.exception("get_tools failed: %s", e)
        return tools

    def get_capabilities_summary(self) -> str:
        """Build a natural-language capability summary for the orchestrator."""
        return self._capabilities_builder.build(
            self._manifests, self._get_manifest, self._setup_providers
        )

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
