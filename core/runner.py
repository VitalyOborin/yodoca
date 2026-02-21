"""Entry point for the AI agent process: bootstrap Loader, Router, Agent; extensions run the UI."""

import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from agents import SQLiteSession

from core.agents.orchestrator import create_orchestrator_agent
from core.events import EventBus
from core.extensions import Loader, MessageRouter
from core.llm import ModelRouter
from core.logging_config import setup_logging
from core.settings import load_settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# Disable SDK tracing when using non-OpenAI or local endpoints (avoids 401s)
from agents import set_tracing_disabled

set_tracing_disabled(True)


async def main_async() -> None:
    """Bootstrap: discover -> load -> init -> wire -> create agent -> start -> wait for shutdown."""
    settings = load_settings()
    setup_logging(_PROJECT_ROOT, settings)

    model_router = ModelRouter(
        settings=settings,
        secrets_getter=os.environ.get,
    )

    extensions_dir = _PROJECT_ROOT / "sandbox" / "extensions"
    data_dir = _PROJECT_ROOT / "sandbox" / "data"
    shutdown_event = asyncio.Event()
    loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir)
    loader.set_shutdown_event(shutdown_event)
    loader.set_model_router(model_router)
    router = MessageRouter()

    eb_cfg = settings.get("event_bus", {})
    db_path = _PROJECT_ROOT / eb_cfg.get("db_path", "sandbox/data/event_journal.db")
    poll_interval = eb_cfg.get("poll_interval", 5.0)
    batch_size = eb_cfg.get("batch_size", 3)
    event_bus = EventBus(
        db_path=db_path,
        poll_interval=poll_interval,
        batch_size=batch_size,
    )
    await event_bus.recover()
    loader.set_event_bus(event_bus)

    await loader.discover()
    await loader.load_all()
    await loader.initialize_all(router)
    loader.detect_and_wire_all(router)
    loader.wire_event_subscriptions(event_bus)

    agent = create_orchestrator_agent(
        model_router=model_router,
        extension_tools=loader.get_all_tools(),
        agent_tools=loader.get_agent_tools(),
        capabilities_summary=loader.get_capabilities_summary(),
    )
    router.set_agent(agent)

    await event_bus.start()
    await loader.start_all()

    session_id = f"orchestrator_{int(time.time())}"
    session_dir = data_dir / "memory"
    session_dir.mkdir(parents=True, exist_ok=True)
    session = SQLiteSession(
        session_id,
        str(session_dir / "session.db"),
    )
    router.set_session(session, session_id)

    loader.wire_context_providers(router)

    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass  # Ctrl+C or supervisor: shutdown gracefully
    finally:
        await event_bus.stop()
        await loader.shutdown()


def main() -> None:
    """Synchronous entry for the AI agent process."""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass  # already handled in main_async via CancelledError; exit cleanly


__all__ = ["main"]
