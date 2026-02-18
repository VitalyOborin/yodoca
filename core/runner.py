"""Entry point for the AI agent process: bootstrap Loader, Router, Agent; extensions run the UI."""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

from core.agents.orchestrator import create_orchestrator_agent
from core.extensions import Loader, MessageRouter

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


async def main_async() -> None:
    """Bootstrap: discover -> load -> init -> wire -> create agent -> start -> wait for shutdown."""
    extensions_dir = _PROJECT_ROOT / "sandbox" / "extensions"
    data_dir = _PROJECT_ROOT / "sandbox" / "data"
    loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir)
    router = MessageRouter()

    await loader.discover()
    await loader.load_all()
    await loader.initialize_all(router)
    loader.detect_and_wire_all(router)

    agent = create_orchestrator_agent(
        extension_tools=loader.get_all_tools(),
        capabilities_summary=loader.get_capabilities_summary(),
    )
    router.set_agent(agent)

    await loader.start_all()

    shutdown_event = asyncio.Event()
    loader.set_shutdown_event(shutdown_event)
    await shutdown_event.wait()
    await loader.shutdown()


def main() -> None:
    """Synchronous entry for the AI agent process."""
    asyncio.run(main_async())


__all__ = ["main"]
