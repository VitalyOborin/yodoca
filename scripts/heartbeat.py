"""Manually trigger the Heartbeat extension's emit_heartbeat task.

Bootstraps the full extension stack (memory, kv, model_router), then calls
HeartbeatExtension.execute_task("emit_heartbeat") — the same logic that runs
when the cron schedule fires (e.g. every 10 minutes).

The Scout agent reviews memory context, decides noop/done/escalate, and
optionally requests an orchestrator task. Use this script to run a heartbeat
on demand without waiting for the schedule.

Usage:
    cd yodoca
    python scripts/heartbeat.py
"""

import asyncio
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from core import secrets

load_dotenv(_PROJECT_ROOT / ".env")


async def main() -> None:
    from core.events import EventBus
    from core.extensions import Loader, MessageRouter
    from core.llm import ModelRouter
    from core.settings import load_settings

    settings = load_settings()
    model_router = ModelRouter(settings=settings, secrets_getter=secrets.get_secret)
    extensions_dir = _PROJECT_ROOT / "sandbox" / "extensions"
    data_dir = _PROJECT_ROOT / "sandbox" / "data"

    loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir, settings=settings)
    loader.set_model_router(model_router)
    router = MessageRouter()

    eb_cfg = settings.get("event_bus", {})
    db_path = _PROJECT_ROOT / eb_cfg.get("db_path", "sandbox/data/event_journal.db")
    event_bus = EventBus(db_path=db_path, poll_interval=5.0, batch_size=3)
    await event_bus.recover()
    loader.set_event_bus(event_bus)

    try:
        await loader.discover()
        await loader.load_all()
        await loader.initialize_all(router)
        loader.detect_and_wire_all(router)

        heartbeat_ext = loader._extensions.get("heartbeat")
        if not heartbeat_ext:
            print("ERROR: Heartbeat extension not loaded.")
            return

        print("Running heartbeat (emit_heartbeat)...")
        t0 = time.monotonic()
        result = await heartbeat_ext.execute_task("emit_heartbeat")
        elapsed = time.monotonic() - t0

        if result is not None:
            print(f"Result: {result}")
        else:
            print("Done (no return value; check logs for noop/done/escalate).")
        print(f"Elapsed: {elapsed:.2f}s")

    finally:
        await loader.shutdown()
        await event_bus.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    sys.exit(0)
