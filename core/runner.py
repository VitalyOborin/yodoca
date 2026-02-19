"""Entry point for the AI agent process: bootstrap Loader, Router, Agent; extensions run the UI."""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from core.agents.orchestrator import create_orchestrator_agent
from core.extensions import Loader, MessageRouter
from core.openai_config import configure_openai_agents_sdk

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


async def main_async() -> None:
    """Bootstrap: discover -> load -> init -> wire -> create agent -> start -> wait for shutdown."""
    configure_openai_agents_sdk()

    extensions_dir = _PROJECT_ROOT / "sandbox" / "extensions"
    data_dir = _PROJECT_ROOT / "sandbox" / "data"
    shutdown_event = asyncio.Event()
    loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir)
    loader.set_shutdown_event(shutdown_event)
    router = MessageRouter()

    await loader.discover()
    await loader.load_all()
    await loader.initialize_all(router)
    loader.detect_and_wire_all(router)

    agent = create_orchestrator_agent(
        extension_tools=loader.get_all_tools(),
        agent_tools=loader.get_agent_tools(),
        capabilities_summary=loader.get_capabilities_summary(),
    )
    router.set_agent(agent)

    await loader.start_all()

    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass  # Ctrl+C or supervisor: shutdown gracefully
    finally:
        await loader.shutdown()


def main() -> None:
    """Synchronous entry for the AI agent process."""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass  # already handled in main_async via CancelledError; exit cleanly


__all__ = ["main"]
