"""Entry point for the AI agent process: bootstrap Loader, Router, Agent; extensions run the UI."""

import asyncio
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core import secrets
from core.agents.deferred_tool_resolver import (
    ToolCatalogEntry,
    make_deferred_tool_tools,
)
from core.agents.delegation_tools import make_delegation_tools
from core.agents.lifecycle import start_lifecycle_loop
from core.agents.orchestrator import create_orchestrator_agent
from core.agents.registry import AgentRegistry
from core.agents.semantic_agent_selector import AgentProfile, SemanticAgentSelector
from core.events import EventBus
from core.extensions import Loader, MessageRouter
from core.llm import EmbeddingCapability, ModelRouter, ModelRouterProtocol
from core.llm.catalog import ModelCatalog
from core.logging_config import setup_logging
from core.settings import get_setting, load_settings
from core.terminal import reset_terminal_for_input
from core.tools.channel import make_channel_tools
from core.tools.secure_input import make_secure_input_tool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _build_event_bus(settings: dict) -> EventBus:
    eb_cfg = settings.get("event_bus", {})
    db_path = _PROJECT_ROOT / eb_cfg.get("db_path", "sandbox/data/event_journal.db")
    return EventBus(
        db_path=db_path,
        poll_interval=eb_cfg.get("poll_interval", 5.0),
        batch_size=eb_cfg.get("batch_size", 3),
        max_retries=eb_cfg.get("max_retries", 3),
        busy_timeout=eb_cfg.get("busy_timeout", 5000),
        stale_timeout=eb_cfg.get("stale_timeout", 300),
    )


def _build_loader_router(settings: dict) -> tuple:
    extensions_dir = _PROJECT_ROOT / "sandbox" / "extensions"
    data_dir = _PROJECT_ROOT / "sandbox" / "data"
    shutdown_event = asyncio.Event()
    loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir, settings=settings)
    loader.set_shutdown_event(shutdown_event)
    router = MessageRouter()
    return loader, router, extensions_dir, data_dir, shutdown_event


async def _wire_extensions(loader: Loader, router: MessageRouter, event_bus: EventBus) -> None:
    await loader.discover()
    await loader.load_all()
    await loader.initialize_all(router)
    loader.detect_and_wire_all(router)
    loader.wire_event_subscriptions(event_bus)


def _create_agent(
    loader: Loader,
    router: MessageRouter,
    event_bus: EventBus,
    settings: dict,
    model_router: ModelRouterProtocol,
    registry: AgentRegistry,
) -> Any:
    from core.agents.factory import AgentFactory

    def tool_resolver(tool_ids: list[str], agent_id: str | None = None) -> list[Any]:
        return loader.resolve_tools(tool_ids, agent_id)

    factory = AgentFactory(model_router, tool_resolver, registry)
    catalog = ModelCatalog(overrides=settings.get("models"))

    embedding_provider = get_setting(settings, "extensions.embedding.provider", None)
    embedding_model = get_setting(settings, "extensions.embedding.default_model", "")
    if embedding_provider:
        embedder = model_router.get_capability(
            EmbeddingCapability,
            provider_id=embedding_provider,
        )
    else:
        embedder = model_router.get_capability(EmbeddingCapability)

    async def _embed_batch(texts: list[str]) -> list[list[float] | None]:
        if not embedder or not embedding_model:
            return [None for _ in texts]
        return await embedder.embed_batch(texts, embedding_model)

    selector = SemanticAgentSelector(
        catalog_getter=lambda: [
            AgentProfile.model_validate(item) for item in loader.get_agent_catalog()
        ],
        db_path=_PROJECT_ROOT / "sandbox" / "data" / "agent_selector.db",
        embed_batch=_embed_batch if embedder and embedding_model else None,
    )

    deferred_tools = make_deferred_tool_tools(
        factory=factory,
        registry=registry,
        catalog_getter=lambda: [
            ToolCatalogEntry.model_validate(entry)
            for entry in loader.get_tool_catalog()
        ],
    )
    channel_tools = make_channel_tools(router) + [make_secure_input_tool(event_bus)]
    return create_orchestrator_agent(
        model_router=model_router,
        settings=settings,
        extension_tools=deferred_tools,
        delegation_tools=make_delegation_tools(
            registry,
            factory,
            loader.get_available_tool_ids,
            catalog,
            selector=selector,
        ),
        capabilities_summary=loader.get_capabilities_summary(),
        channel_tools=channel_tools,
    )


def _configure_session_and_context(
    router: MessageRouter,
    loader: Loader,
    agent: Any,
    settings: dict,
    data_dir: Path,
    event_bus: EventBus,
) -> None:
    mcp_servers = loader.get_mcp_servers()
    if mcp_servers:
        agent.mcp_servers = mcp_servers
        agent.mcp_config = {"convert_schemas_to_strict": True}
    session_timeout = get_setting(settings, "session.timeout_sec", 1800)
    session_dir = data_dir / "memory"
    session_dir.mkdir(parents=True, exist_ok=True)
    router.configure_session(
        session_db_path=str(session_dir / "session.db"),
        session_timeout=session_timeout,
        event_bus=event_bus,
    )
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
    await _wire_extensions(loader, router, event_bus)
    agent = _create_agent(loader, router, event_bus, settings, model_router, registry)
    router.set_agent(agent)
    _configure_session_and_context(router, loader, agent, settings, data_dir, event_bus)
    await event_bus.start()
    await loader.start_all()
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
