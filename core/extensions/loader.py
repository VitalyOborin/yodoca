"""Loader: discover, load, initialize, wire, start extensions. Manages lifecycle and protocol detection."""

import asyncio
import importlib.util
import logging
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any

from croniter import croniter

from core.extensions.contract import (
    AgentDescriptor,
    AgentProvider,
    ChannelProvider,
    Extension,
    ServiceProvider,
    SchedulerProvider,
    ToolProvider,
)
from core.extensions.context import ExtensionContext
from core.extensions.instructions import resolve_instructions
from core.extensions.manifest import ExtensionManifest, load_manifest
from core.extensions.router import MessageRouter

logger = logging.getLogger(__name__)

_HEALTH_CHECK_INTERVAL = 30.0
_CRON_TICK_SEC = 60


class ExtensionState(Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    ERROR = "error"


class Loader:
    """Extension lifecycle: discover -> load -> initialize -> detect -> wire -> start."""

    def __init__(
        self,
        extensions_dir: Path,
        data_dir: Path,
    ) -> None:
        self._extensions_dir = extensions_dir
        self._data_dir = data_dir
        self._router: MessageRouter | None = None
        self._manifests: list[ExtensionManifest] = []
        self._extensions: dict[str, Extension] = {}
        self._state: dict[str, ExtensionState] = {}
        self._tool_providers: list[ToolProvider] = []
        self._agent_providers: dict[str, AgentProvider] = {}
        self._service_tasks: dict[str, asyncio.Task[Any]] = {}
        self._schedulers: dict[str, SchedulerProvider] = {}
        self._scheduler_next: dict[str, float] = {}
        self._cron_task: asyncio.Task[Any] | None = None
        self._health_task: asyncio.Task[Any] | None = None
        self._shutdown_event: asyncio.Event | None = None

    def set_shutdown_event(self, event: asyncio.Event) -> None:
        self._shutdown_event = event

    async def discover(self) -> None:
        """Scan extensions_dir for manifest.yaml; load and filter enabled."""
        self._manifests = []
        if not self._extensions_dir.exists():
            return
        for d in sorted(self._extensions_dir.iterdir()):
            if not d.is_dir():
                continue
            manifest_path = d / "manifest.yaml"
            if not manifest_path.exists():
                continue
            try:
                manifest = load_manifest(manifest_path)
                if manifest.enabled:
                    self._manifests.append(manifest)
            except Exception as e:
                logger.exception("Invalid manifest %s: %s", manifest_path, e)

    def _resolve_dependency_order(self) -> list[ExtensionManifest]:
        """Topological sort by depends_on. Raises on cycle or missing dep."""
        ids = {m.id for m in self._manifests}
        for m in self._manifests:
            for dep in m.depends_on:
                if dep not in ids:
                    raise ValueError(f"Extension {m.id} depends on missing {dep}")
        order: list[ExtensionManifest] = []
        seen: set[str] = set()
        visiting: set[str] = set()

        def visit(m: ExtensionManifest) -> None:
            if m.id in seen:
                return
            if m.id in visiting:
                raise ValueError(f"Cycle in depends_on involving {m.id}")
            visiting.add(m.id)
            for dep in m.depends_on:
                dep_m = next((x for x in self._manifests if x.id == dep), None)
                if dep_m:
                    visit(dep_m)
            visiting.remove(m.id)
            seen.add(m.id)
            order.append(m)

        for m in self._manifests:
            visit(m)
        return order

    async def load_all(self) -> None:
        """Load extension modules in dependency order; instantiate."""
        order = self._resolve_dependency_order()
        self._extensions = {}
        self._state = {}
        for manifest in order:
            try:
                ext = self._load_one(manifest)
                self._extensions[manifest.id] = ext
                self._state[manifest.id] = ExtensionState.INACTIVE
            except Exception as e:
                logger.exception("Failed to load extension %s: %s", manifest.id, e)

    def _load_one(self, manifest: ExtensionManifest) -> Extension:
        """Dynamic import or declarative adapter. Declarative agents need no main.py."""
        if manifest.agent and not manifest.entrypoint:
            from core.extensions.declarative_agent import DeclarativeAgentAdapter
            return DeclarativeAgentAdapter(manifest)
        ext_dir = self._extensions_dir / manifest.id
        assert manifest.entrypoint is not None
        module_name, class_name = manifest.entrypoint.split(":", 1)
        py_path = ext_dir / f"{module_name}.py"
        if not py_path.exists():
            raise FileNotFoundError(f"{py_path} not found")
        spec = importlib.util.spec_from_file_location(
            f"ext_{manifest.id}_{module_name}", py_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {py_path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        cls = getattr(mod, class_name)
        return cls()

    def _get_extension(self, ext_id: str) -> Any:
        """Return extension instance only if in depends_on of current extension (used by context)."""
        return self._extensions.get(ext_id)

    def _resolve_agent_tools(self, manifest: ExtensionManifest) -> list[Any]:
        """Resolve uses_tools to actual tools from ToolProvider extensions or core_tools."""
        if not manifest.agent:
            return []
        tools: list[Any] = []
        for ext_id in manifest.agent.uses_tools:
            if ext_id == "core_tools":
                from core.tools.provider import CoreToolsProvider
                tools.extend(CoreToolsProvider().get_tools())
                continue
            ext = self._extensions.get(ext_id)
            if ext and isinstance(ext, ToolProvider):
                tools.extend(ext.get_tools())
        return tools

    def _resolve_agent_instructions(self, manifest: ExtensionManifest, ext_id: str) -> str:
        """Resolve instructions from agent.instructions and agent.instructions_file (kernel helper)."""
        if not manifest.agent:
            return ""
        extension_dir = self._extensions_dir / ext_id
        project_root = self._extensions_dir.parent.parent
        return resolve_instructions(
            instructions=manifest.agent.instructions,
            instructions_file=manifest.agent.instructions_file,
            extension_dir=extension_dir,
            project_root=project_root,
        )

    async def initialize_all(self, router: MessageRouter) -> None:
        """Create context per extension, call initialize(ctx). Skip on exception."""
        self._router = router
        for ext_id, ext in list(self._extensions.items()):
            if self._state.get(ext_id) != ExtensionState.INACTIVE:
                continue
            manifest = next(m for m in self._manifests if m.id == ext_id)
            data_dir_path = self._data_dir / ext_id
            resolved_tools = self._resolve_agent_tools(manifest) if manifest.agent else []
            resolved_instructions = (
                self._resolve_agent_instructions(manifest, ext_id) if manifest.agent else ""
            )
            agent_model = manifest.agent.model if manifest.agent else ""
            ctx = ExtensionContext(
                extension_id=ext_id,
                config=manifest.config,
                logger=logging.getLogger(f"ext.{ext_id}"),
                router=router,
                get_extension=self._get_extension,
                data_dir_path=data_dir_path,
                shutdown_event=self._shutdown_event,
                resolved_tools=resolved_tools,
                resolved_instructions=resolved_instructions,
                agent_model=agent_model,
            )
            try:
                await ext.initialize(ctx)
            except Exception as e:
                logger.exception("initialize failed for %s: %s", ext_id, e)
                self._state[ext_id] = ExtensionState.ERROR

    def detect_and_wire_all(self, router: MessageRouter) -> None:
        """Detect protocols via isinstance; wire ToolProvider, ChannelProvider, etc."""
        self._tool_providers = []
        self._agent_providers = {}
        self._schedulers = {}
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            if isinstance(ext, ToolProvider):
                self._tool_providers.append(ext)
            if isinstance(ext, AgentProvider):
                self._agent_providers[ext_id] = ext
            if isinstance(ext, ChannelProvider):
                router.register_channel(ext_id, ext)
            if isinstance(ext, SchedulerProvider):
                self._schedulers[ext_id] = ext

    async def start_all(self) -> None:
        """Call start() on all; wrap ServiceProvider.run_background in tasks; start cron loop."""
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.INACTIVE:
                continue
            try:
                await ext.start()
                self._state[ext_id] = ExtensionState.ACTIVE
                if isinstance(ext, ServiceProvider):
                    self._service_tasks[ext_id] = asyncio.create_task(ext.run_background())
            except Exception as e:
                logger.exception("start failed for %s: %s", ext_id, e)
                self._state[ext_id] = ExtensionState.ERROR
        self._cron_task = asyncio.create_task(self._cron_loop())
        self._health_task = asyncio.create_task(self._health_check_loop())

    def get_all_tools(self) -> list[Any]:
        """Collect tools from all ToolProvider extensions."""
        tools: list[Any] = []
        for ext in self._tool_providers:
            try:
                tools.extend(ext.get_tools())
            except Exception as e:
                logger.exception("get_tools failed: %s", e)
        return tools

    def get_agent_tools(self) -> list[Any]:
        """Wrap AgentProvider extensions (tool mode) as callable tools for the Orchestrator."""
        tools: list[Any] = []
        for ext_id, ext in self._agent_providers.items():
            descriptor = ext.get_agent_descriptor()
            if descriptor.integration_mode == "tool":
                tools.append(self._wrap_agent_as_tool(ext_id, ext, descriptor))
        return tools

    def _wrap_agent_as_tool(
        self, ext_id: str, ext: AgentProvider, descriptor: AgentDescriptor
    ) -> Any:
        from agents import function_tool

        async def invoke_agent(task: str) -> str:
            response = await ext.invoke(task)
            if response.status == "success":
                return response.content
            return f"Agent error: {response.error or response.content}"

        invoke_agent.__doc__ = descriptor.description or ""
        return function_tool(name_override=ext_id)(invoke_agent)

    def get_capabilities_summary(self) -> str:
        """Natural-language summary: tools and agents separate for orchestrator prompt."""
        tool_parts: list[str] = []
        agent_parts: list[str] = []
        for m in self._manifests:
            if m.id not in self._extensions or self._state.get(m.id) == ExtensionState.ERROR:
                continue
            if not m.description:
                continue
            desc = m.description.strip()
            if m.id in self._agent_providers:
                agent_parts.append(f"- {m.id}: {desc}")
            else:
                tool_parts.append(f"- {m.id}: {desc}")
        sections: list[str] = []
        if tool_parts:
            sections.append("Available tools:\n" + "\n".join(tool_parts))
        if agent_parts:
            sections.append("Available agents:\n" + "\n".join(agent_parts))
        return "\n\n".join(sections) if sections else "No extensions loaded."

    async def _health_check_loop(self) -> None:
        """Every 30s call health_check(); on False set ERROR and stop()."""
        while True:
            await asyncio.sleep(_HEALTH_CHECK_INTERVAL)
            for ext_id, ext in list(self._extensions.items()):
                if self._state.get(ext_id) != ExtensionState.ACTIVE:
                    continue
                try:
                    if not ext.health_check():
                        self._state[ext_id] = ExtensionState.ERROR
                        await ext.stop()
                except Exception as e:
                    logger.exception("health_check failed for %s: %s", ext_id, e)
                    self._state[ext_id] = ExtensionState.ERROR
                    await ext.stop()

    async def _cron_loop(self) -> None:
        """Every minute evaluate SchedulerProvider schedules; call execute() on match."""
        now = time.time()
        for ext_id in self._schedulers:
            try:
                c = croniter(self._schedulers[ext_id].get_schedule(), now)
                self._scheduler_next[ext_id] = c.get_next(float)
            except Exception:
                self._scheduler_next[ext_id] = now + 86400
        while True:
            await asyncio.sleep(_CRON_TICK_SEC)
            if not self._router:
                continue
            now = time.time()
            for ext_id, ext in list(self._schedulers.items()):
                if self._state.get(ext_id) != ExtensionState.ACTIVE:
                    continue
                next_run = self._scheduler_next.get(ext_id, 0)
                if now < next_run:
                    continue
                try:
                    result = await ext.execute()
                    self._scheduler_next[ext_id] = croniter(
                        ext.get_schedule(), next_run
                    ).get_next(float)
                    if result and isinstance(result, dict) and "text" in result:
                        await self._router.notify_user(result["text"])
                except Exception as e:
                    logger.exception("scheduler execute failed for %s: %s", ext_id, e)

    async def shutdown(self) -> None:
        """Stop then destroy all extensions in reverse order."""
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        if self._cron_task:
            self._cron_task.cancel()
            try:
                await self._cron_task
            except asyncio.CancelledError:
                pass
        for task in self._service_tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
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
