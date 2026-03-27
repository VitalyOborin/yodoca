"""Entry point for the AI agent process: bootstrap Loader, Router, Agent; extensions run the UI."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core import secrets
from core.agents.delegation_tools import make_delegation_tools
from core.agents.factory import AgentFactory
from core.agents.lifecycle import start_lifecycle_loop
from core.agents.registry import AgentRegistry
from core.events import EventBus
from core.extensions import Loader, MessageRouter
from core.extensions.contract import AgentProvider
from core.llm import ModelRouter
from core.llm.catalog import ModelCatalog
from core.logging_config import setup_logging
from core.settings import load_settings
from core.settings_models import AppSettings
from core.terminal import reset_terminal_for_input
from core.tools.channel import make_channel_tools
from core.tools.configure_extension import make_configure_extension_tool
from core.tools.extensions_doctor import make_extensions_doctor_tool
from core.tools.secure_input import make_secure_input_tool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


def _build_event_bus(settings: AppSettings) -> EventBus:
    eb = settings.event_bus
    db_path = _PROJECT_ROOT / eb.db_path
    return EventBus(
        db_path=db_path,
        poll_interval=eb.poll_interval,
        batch_size=eb.batch_size,
        max_retries=eb.max_retries,
        busy_timeout=eb.busy_timeout,
        stale_timeout=eb.stale_timeout,
        handler_timeout=eb.handler_timeout,
    )


def _build_loader_router(
    settings: AppSettings,
) -> tuple[Loader, MessageRouter, Path, Path, asyncio.Event]:
    extensions_dir = _PROJECT_ROOT / "sandbox" / "extensions"
    data_dir = _PROJECT_ROOT / "sandbox" / "data"
    shutdown_event = asyncio.Event()
    loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir, settings=settings)
    loader.set_shutdown_event(shutdown_event)
    router = MessageRouter()
    return loader, router, extensions_dir, data_dir, shutdown_event


async def _wire_extensions(
    loader: Loader, router: MessageRouter, event_bus: EventBus
) -> None:
    await loader.discover()
    await loader.load_all()
    await loader.initialize_all(router)
    await loader.update_setup_providers_state()
    loader.detect_and_wire_all(router)
    loader.wire_event_subscriptions(event_bus)


def _resolve_default_agent(
    loader: Loader,
    router: MessageRouter,
    event_bus: EventBus,
    settings: AppSettings,
    model_router: ModelRouter,
    registry: AgentRegistry,
) -> Any:
    """Load the declarative default-agent extension and attach the full orchestrator tool set."""
    ext_id = (settings.default_agent or "").strip()
    if not ext_id:
        raise RuntimeError("default_agent not configured in settings")

    ext = loader.get_extensions().get(ext_id)
    if ext is None:
        raise RuntimeError(f"default_agent {ext_id!r} not found in loaded extensions")
    if not isinstance(ext, AgentProvider):
        raise RuntimeError(f"default_agent {ext_id!r} does not implement AgentProvider")
    inner = getattr(ext, "agent", None)
    if inner is None:
        raise RuntimeError(
            f"default_agent {ext_id!r} has no initialized agent "
            "(extension initialize() did not run or is not a declarative agent)"
        )

    def tool_resolver(tool_ids: list[str], agent_id: str | None = None) -> list[Any]:
        return loader.resolve_tools(tool_ids, agent_id)

    factory = AgentFactory(model_router, tool_resolver, registry)
    catalog = ModelCatalog(
        overrides={
            mid: m.model_dump(mode="python") for mid, m in settings.models.items()
        }
    )
    channel_tools = make_channel_tools(router) + [
        make_secure_input_tool(event_bus),
        make_configure_extension_tool(
            loader.get_extensions(),
            secret_resolver=secrets.get_secret_async,
        ),
        make_extensions_doctor_tool(loader.get_extension_status_report),
    ]
    delegation_tools = make_delegation_tools(
        registry=registry,
        factory=factory,
        get_available_tool_ids=loader.get_available_tool_ids,
        catalog=catalog,
        get_tool_catalog=loader.get_tool_catalog,
    )
    tools: list[Any] = []
    tools.extend(loader.get_all_tools())
    tools.extend(delegation_tools)
    tools.extend(channel_tools)

    inner.tools = tools
    return inner


def _configure_thread(
    router: MessageRouter,
    settings: AppSettings,
    data_dir: Path,
    event_bus: EventBus,
) -> None:
    thread_timeout = settings.thread.timeout_sec
    thread_dir = data_dir / "memory"
    thread_dir.mkdir(parents=True, exist_ok=True)
    router.configure_thread(
        thread_db_path=str(thread_dir / "thread.db"),
        thread_timeout=thread_timeout,
        event_bus=event_bus,
    )


def _configure_agent_mcp_and_context(
    loader: Loader,
    router: MessageRouter,
    agent: Any,
) -> None:
    mcp_servers = loader.get_mcp_servers()
    if mcp_servers:
        agent.mcp_servers = mcp_servers
        agent.mcp_config = {"convert_schemas_to_strict": True}
    loader.wire_context_providers(router)


async def main_async() -> None:
    """Bootstrap: discover -> load -> init -> wire -> create agent -> start -> wait for shutdown."""
    settings = load_settings()
    setup_logging(_PROJECT_ROOT, settings)
    model_router = ModelRouter(settings=settings, secrets_getter=secrets.get_secret)
    registry = AgentRegistry(on_unregister=model_router.remove_agent_config)
    loader, router, _ext_dir, data_dir, shutdown_event = _build_loader_router(settings)
    loader.set_model_router(model_router)
    loader.set_agent_registry(registry)
    event_bus = _build_event_bus(settings)
    await event_bus.recover()
    loader.set_event_bus(event_bus)
    _configure_thread(router, settings, data_dir, event_bus)
    await _wire_extensions(loader, router, event_bus)
    agent = _resolve_default_agent(
        loader, router, event_bus, settings, model_router, registry
    )
    router.set_agent(agent, agent_id=settings.default_agent)
    _configure_agent_mcp_and_context(loader, router, agent)
    await event_bus.start()
    await loader.start_all()
    report = loader.get_extension_status_report()
    counts = report.get("counts", {})
    errors = [
        f"{item['extension_id']}={item['latest_diagnostic']['reason']}"
        for item in report.get("extensions", [])
        if item.get("state") == "error" and item.get("latest_diagnostic")
    ]
    logger.info(
        "Extensions: %s active, %s inactive, %s error%s",
        counts.get("active", 0),
        counts.get("inactive", 0),
        counts.get("error", 0),
        f" ({', '.join(errors[:5])})" if errors else "",
    )
    lifecycle_task = start_lifecycle_loop(registry, interval_seconds=60.0)
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        lifecycle_task.cancel()
        try:
            await lifecycle_task
        except asyncio.CancelledError:
            pass
        await event_bus.stop()
        await loader.shutdown()


def main() -> None:
    """Synchronous entry for the AI agent process."""
    load_dotenv(_PROJECT_ROOT / ".env")
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
    reset_terminal_for_input()
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass  # already handled in main_async via CancelledError; exit cleanly


__all__ = ["main"]
