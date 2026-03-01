"""Loader: discover, load, initialize, wire, start extensions. Manages lifecycle and protocol detection."""

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Callable

from core.events import EventBus
from core.llm import ModelRouterProtocol
from core.events.models import Event
from core.events.topics import SystemTopics
from core.extensions.builtin_context import ActiveChannelContextProvider
from core.extensions.contract import (
    AgentDescriptor,
    AgentInvocationContext,
    AgentProvider,
    ChannelProvider,
    ContextProvider,
    Extension,
    ExtensionState,
    ServiceProvider,
    SchedulerProvider,
    ToolProvider,
    TurnContext,
)
from core.extensions.context import ExtensionContext
from core.extensions.health_check import HealthCheckManager
from core.extensions.instructions import resolve_instructions
from core.extensions.manifest import ExtensionManifest, load_manifest
from core.extensions.router import MessageRouter
from core.extensions.scheduler_manager import SchedulerManager
from core.settings import get_setting

logger = logging.getLogger(__name__)


class Loader:
    """Extension lifecycle: discover -> load -> initialize -> detect -> wire -> start."""

    def __init__(
        self,
        extensions_dir: Path,
        data_dir: Path,
        settings: dict[str, Any],
    ) -> None:
        self._extensions_dir = extensions_dir
        self._data_dir = data_dir
        self._settings = settings
        self._router: MessageRouter | None = None
        self._model_router: ModelRouterProtocol | None = None
        self._manifests: list[ExtensionManifest] = []
        self._extensions: dict[str, Extension] = {}
        self._state: dict[str, ExtensionState] = {}
        self._tool_providers: list[ToolProvider] = []
        self._agent_providers: dict[str, AgentProvider] = {}
        self._service_tasks: dict[str, asyncio.Task[Any]] = {}
        self._health_manager = HealthCheckManager(self._extensions, self._state)
        self._scheduler_manager: SchedulerManager | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._event_bus: EventBus | None = None

    def set_shutdown_event(self, event: asyncio.Event) -> None:
        self._shutdown_event = event

    def set_model_router(self, model_router: ModelRouterProtocol | None) -> None:
        """Inject ModelRouter for agent model resolution (core/llm)."""
        self._model_router = model_router

    def set_event_bus(self, event_bus: EventBus) -> None:
        """Inject EventBus for durable event flows."""
        self._event_bus = event_bus

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
        if manifest.entrypoint is None:
            raise ValueError(
                f"Extension {manifest.id} must have entrypoint for programmatic extensions"
            )
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

    def _make_get_extension(self, caller_ext_id: str) -> Callable[[str], Any]:
        """Return a get_extension callable that enforces depends_on for the caller."""
        caller_manifest = next(m for m in self._manifests if m.id == caller_ext_id)
        allowed = set(caller_manifest.depends_on)

        def get_extension(ext_id: str) -> Any:
            if ext_id not in allowed:
                raise ValueError(
                    f"Extension '{caller_ext_id}' cannot access '{ext_id}': "
                    f"not in depends_on ({allowed})"
                )
            return self._extensions.get(ext_id)

        return get_extension

    def _get_restart_file_path(self) -> Path:
        """Restart flag file path from supervisor.restart_file setting (project-root-relative)."""
        return self._data_dir.parent.parent / get_setting(
            self._settings, "supervisor.restart_file", "sandbox/.restart_requested"
        )

    def _resolve_agent_tools(self, manifest: ExtensionManifest) -> list[Any]:
        """Resolve uses_tools to actual tools from ToolProvider extensions or core_tools."""
        if not manifest.agent:
            return []
        tools: list[Any] = []
        for ext_id in manifest.agent.uses_tools:
            if ext_id == "core_tools":
                from core.tools.provider import CoreToolsProvider

                agent_id = getattr(manifest, "agent_id", None) or manifest.id
                restart_file_path = self._get_restart_file_path()
                tools.extend(
                    CoreToolsProvider(
                        model_router=self._model_router,
                        agent_id=agent_id,
                        restart_file_path=restart_file_path,
                    ).get_tools()
                )
                continue
            ext = self._extensions.get(ext_id)
            if ext and isinstance(ext, ToolProvider):
                tools.extend(ext.get_tools())
        return tools

    def _resolve_agent_instructions(
        self, manifest: ExtensionManifest, ext_id: str
    ) -> str:
        """Resolve instructions from extension-local prompt.jinja2 and/or agent.instructions.

        Agent extensions may have prompt.jinja2 in extension/<id>/. If present, it is used.
        Manifest instructions (optional) are merged: file content first, then inline instructions.
        Only extension dir is searched; project prompts/ is system-only and not used.
        """
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

    def _register_agent_config_from_manifests(self) -> None:
        """Register agent config from manifests with model_router (default + overrides)."""
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
        data_dir_path = self._data_dir / ext_id
        overrides = self._settings.get("extensions", {}).get(ext_id, {}) or {}
        config = {**manifest.config, **overrides}
        resolved_tools = self._resolve_agent_tools(manifest) if manifest.agent else []
        resolved_instructions = (
            self._resolve_agent_instructions(manifest, ext_id) if manifest.agent else ""
        )
        agent_model = manifest.agent.model if manifest.agent else ""
        agent_id = getattr(manifest, "agent_id", None) or (ext_id if manifest.agent else None)
        return ExtensionContext(
            extension_id=ext_id,
            config=config,
            logger=logging.getLogger(f"ext.{ext_id}"),
            router=router,
            get_extension=self._make_get_extension(ext_id),
            data_dir_path=data_dir_path,
            shutdown_event=self._shutdown_event,
            resolved_tools=resolved_tools,
            resolved_instructions=resolved_instructions,
            agent_model=agent_model,
            model_router=self._model_router,
            agent_id=agent_id,
            event_bus=self._event_bus,
            restart_file_path=self._get_restart_file_path(),
        )

    async def initialize_all(self, router: MessageRouter) -> None:
        """Create context per extension, call initialize(ctx). Skip on exception."""
        self._router = router
        self._register_agent_config_from_manifests()
        for ext_id, ext in list(self._extensions.items()):
            if self._state.get(ext_id) != ExtensionState.INACTIVE:
                continue
            manifest = next(m for m in self._manifests if m.id == ext_id)
            ctx = self._build_extension_context(ext_id, manifest, router)
            try:
                await ext.initialize(ctx)
            except Exception as e:
                logger.exception("initialize failed for %s: %s", ext_id, e)
                self._state[ext_id] = ExtensionState.ERROR

    def _collect_proactive_subscriptions(self) -> dict[str, str]:
        """Return {topic: ext_id} for invoke_agent subscriptions. First in manifest order wins."""
        result: dict[str, str] = {}
        for manifest in self._manifests:
            if not manifest.events or not manifest.events.subscribes:
                continue
            ext_id = manifest.id
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            if ext_id not in self._agent_providers:
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
        if self._router:
            prompt = event.payload.get("prompt", "")
            channel_id = event.payload.get("channel_id")
            turn_context = TurnContext(
                agent_id="orchestrator",
                channel_id=channel_id,
            )
            response = await self._router.invoke_agent_background(
                prompt, turn_context=turn_context
            )
            if response:
                await self._router.notify_user(response, channel_id)

    async def _on_agent_background(self, event: Event) -> None:
        import time as _time

        if not self._router:
            return
        prompt = event.payload.get("prompt", "")
        correlation_id = event.payload.get("correlation_id") or event.correlation_id
        started_at = _time.perf_counter()
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
            duration_ms = int((_time.perf_counter() - started_at) * 1000)
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
        if not self._router:
            return
        event_bus.subscribe(SystemTopics.USER_NOTIFY, self._on_user_notify, "kernel.system")
        event_bus.subscribe(SystemTopics.AGENT_TASK, self._on_agent_task, "kernel.system")
        event_bus.subscribe(
            SystemTopics.AGENT_BACKGROUND, self._on_agent_background, "kernel.system"
        )

    def _wire_notify_user_handlers(self, event_bus: EventBus) -> None:
        router = self._router
        if not router:
            return
        for manifest in self._manifests:
            if not manifest.events or not manifest.events.subscribes:
                continue
            ext_id = manifest.id
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            for sub in manifest.events.subscribes:
                if sub.handler != "notify_user":
                    continue
                async def handler(event: Event) -> None:
                    if self._router:
                        await self._router.notify_user(event.payload.get("text", ""))
                event_bus.subscribe(sub.topic, handler, ext_id)

    async def _on_kernel_user_message(self, event: Event) -> None:
        if not self._router:
            return
        router = self._router
        text = event.payload.get("text", "").strip()
        user_id = event.payload.get("user_id", "default")
        channel_id = event.payload.get("channel_id")
        if not text or not channel_id:
            logger.warning(
                "user.message missing text or channel_id: %s", event.payload
            )
            return
        channel = router.get_channel(channel_id)
        if not channel:
            logger.warning("user.message: unknown channel_id %s", channel_id)
            return
        await router.handle_user_message(text, user_id, channel, channel_id)

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
        router = self._router
        if not router:
            return
        proactive_map = self._collect_proactive_subscriptions()
        for topic, ext_id in proactive_map.items():
            agent = self._agent_providers.get(ext_id)
            if not agent:
                logger.debug(
                    "Proactive topic %s: ext %s is not AgentProvider, skip",
                    topic,
                    ext_id,
                )
                continue
            handler = self._make_proactive_handler(topic, ext_id, agent)
            event_bus.subscribe(topic, handler, "kernel.proactive")

    def wire_event_subscriptions(self, event_bus: EventBus) -> None:
        """Wire manifest-driven notify_user and invoke_agent handlers. Call after detect_and_wire_all."""
        if not self._router:
            return
        self._wire_system_topics(event_bus)
        self._wire_notify_user_handlers(event_bus)
        event_bus.subscribe("user.message", self._on_kernel_user_message, "kernel")
        self._wire_proactive_handlers(event_bus)

    def _collect_context_providers(
        self, router: MessageRouter
    ) -> list[ContextProvider]:
        """Collect ContextProvider extensions (ACTIVE only) plus built-in ActiveChannelContextProvider, sorted by context_priority."""
        providers: list[ContextProvider] = [
            ActiveChannelContextProvider(router),
        ]
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

        The middleware returns context to inject into the system role (empty string = no context),
        not an enriched user message. The router uses this for system injection via agent.clone().
        """
        providers = self._collect_context_providers(router)
        if not providers:
            return

        async def _middleware(prompt: str, turn_context: TurnContext) -> str:
            """Return context to inject into system role (empty string = no context). Not an enriched user message."""
            parts: list[str] = []
            for provider in providers:
                ctx = await provider.get_context(prompt, turn_context)
                if ctx:
                    parts.append(ctx)
            if not parts:
                return ""
            return "\n\n---\n\n".join(parts)

        router.set_invoke_middleware(_middleware)

    def detect_and_wire_all(self, router: MessageRouter) -> None:
        """Detect protocols via isinstance; wire ToolProvider, ChannelProvider, etc."""
        self._tool_providers = []
        self._agent_providers = {}
        self._scheduler_manager = SchedulerManager(state=self._state, router=router)
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            manifest = next((m for m in self._manifests if m.id == ext_id), None)
            if isinstance(ext, ToolProvider):
                self._tool_providers.append(ext)
            if isinstance(ext, AgentProvider):
                self._agent_providers[ext_id] = ext
            if isinstance(ext, ChannelProvider):
                router.register_channel(ext_id, ext)
            if isinstance(ext, SchedulerProvider) and manifest:
                self._scheduler_manager.register(ext_id, ext, manifest)

        channel_ids = {
            eid for eid, e in self._extensions.items()
            if isinstance(e, ChannelProvider)
        }
        channel_descriptions = {
            m.id: m.name for m in self._manifests if m.id in channel_ids
        }
        router.set_channel_descriptions(channel_descriptions)

    async def start_all(self) -> None:
        """Call start() on all; wrap ServiceProvider.run_background in tasks; start cron loop."""
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.INACTIVE:
                continue
            try:
                await ext.start()
                self._state[ext_id] = ExtensionState.ACTIVE
                if isinstance(ext, ServiceProvider):
                    self._service_tasks[ext_id] = asyncio.create_task(
                        ext.run_background()
                    )
            except Exception as e:
                logger.exception("start failed for %s: %s", ext_id, e)
                self._state[ext_id] = ExtensionState.ERROR
        if self._scheduler_manager:
            self._scheduler_manager.start()
        self._health_manager.start()

    def get_mcp_servers(self) -> list[Any]:
        """Collect MCP server instances from ACTIVE extensions that provide get_mcp_servers (duck-typed)."""
        servers: list[Any] = []
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.ACTIVE:
                continue
            if not hasattr(ext, "get_mcp_servers") or not callable(
                getattr(ext, "get_mcp_servers")
            ):
                continue
            try:
                result = ext.get_mcp_servers()
                if result:
                    servers.extend(result if isinstance(result, list) else list(result))
            except Exception as e:
                logger.exception("get_mcp_servers failed for %s: %s", ext_id, e)
        return servers

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

    def _collect_tool_agent_parts(self) -> tuple[list[str], list[str]]:
        """Return (tool_parts, agent_parts) for capabilities summary."""
        tool_parts: list[str] = []
        agent_parts: list[str] = []
        for m in self._manifests:
            if (
                m.id not in self._extensions
                or self._state.get(m.id) == ExtensionState.ERROR
            ):
                continue
            if not m.description:
                continue
            desc = m.description.strip()
            if m.id in self._agent_providers:
                agent_parts.append(f"- {m.id}: {desc}")
            else:
                tool_parts.append(f"- {m.id}: {desc}")
        return (tool_parts, agent_parts)

    def _collect_mcp_aliases(self) -> list[str]:
        """Collect MCP server aliases from ACTIVE extensions."""
        mcp_aliases: list[str] = []
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.ACTIVE:
                continue
            if not hasattr(ext, "get_mcp_server_aliases") or not callable(
                getattr(ext, "get_mcp_server_aliases")
            ):
                continue
            try:
                aliases = ext.get_mcp_server_aliases()
                if aliases:
                    mcp_aliases.extend(
                        aliases if isinstance(aliases, list) else list(aliases)
                    )
            except Exception as e:
                logger.exception(
                    "get_mcp_server_aliases failed for %s: %s", ext_id, e
                )
        return mcp_aliases

    def get_capabilities_summary(self) -> str:
        """Natural-language summary: tools, agents, and MCP servers for orchestrator prompt."""
        tool_parts, agent_parts = self._collect_tool_agent_parts()
        mcp_aliases = self._collect_mcp_aliases()
        sections: list[str] = []
        if tool_parts:
            sections.append("Available tools:\n" + "\n".join(tool_parts))
        if agent_parts:
            sections.append("Available agents:\n" + "\n".join(agent_parts))
        if mcp_aliases:
            mcp_lines = "\n".join(f"- {a}" for a in mcp_aliases)
            sections.append("MCP servers:\n" + mcp_lines)
        return "\n\n".join(sections) if sections else "No extensions loaded."

    async def shutdown(self) -> None:
        """Stop then destroy all extensions in reverse order."""
        await self._health_manager.stop()
        if self._scheduler_manager:
            await self._scheduler_manager.stop()
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
