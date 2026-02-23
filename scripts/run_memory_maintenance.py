"""Manually trigger the Memory v2 nightly maintenance pipeline.

Bootstraps the full extension stack (including embedding + model_router),
then calls MemoryExtension.execute_task("run_nightly_maintenance").

Pipeline steps:
  1. Consolidate pending sessions (LLM write-path agent)
  2. Apply Ebbinghaus decay + prune low-confidence nodes
  3. Enrich sparse entities (LLM summaries)
  4. Infer causal edges between consecutive episodes (LLM)

Usage:
    cd assistant4
    python scripts/run_memory_maintenance.py [--dry-run]

Options:
    --dry-run   Show stats only; skip consolidation, decay, and LLM calls.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")


async def print_stats(storage: object) -> None:
    stats = await storage.get_graph_stats()
    size_mb = storage.get_storage_size_mb()
    unconsolidated = await storage.get_unconsolidated_sessions()

    print("\n=== Memory Graph Stats ===")
    print(f"  Nodes: {stats.get('nodes', {})}")
    print(f"  Edges: {stats.get('edges', {})}")
    print(f"  Entities: {stats.get('entities', 0)}")
    print(f"  Orphan nodes: {stats.get('orphan_nodes', 0)}")
    print(f"  Avg edges/node: {stats.get('avg_edges_per_node', 0):.2f}")
    print(f"  Unconsolidated sessions: {len(unconsolidated)} {unconsolidated}")
    print(f"  Storage size: {size_mb:.2f} MB\n")


async def main() -> None:
    dry_run = "--dry-run" in sys.argv

    from core.events import EventBus
    from core.extensions import Loader, MessageRouter
    from core.llm import ModelRouter
    from core.settings import load_settings

    settings = load_settings()
    model_router = ModelRouter(settings=settings, secrets_getter=os.environ.get)
    extensions_dir = _PROJECT_ROOT / "sandbox" / "extensions"
    data_dir = _PROJECT_ROOT / "sandbox" / "data"

    loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir)
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

        mem_ext = loader._extensions.get("memory")
        if not mem_ext or not mem_ext._storage:
            print("ERROR: Memory extension not loaded or storage not initialized.")
            return

        await print_stats(mem_ext._storage)

        if dry_run:
            print("[dry-run] Skipping maintenance pipeline. Use without --dry-run to execute.")
            return

        print("Running nightly maintenance pipeline...")
        t0 = time.monotonic()
        result = await mem_ext.execute_task("run_nightly_maintenance")
        elapsed = time.monotonic() - t0

        if result:
            print(f"\nResult: {result.get('text', result)}")
        else:
            print("\nResult: None (no work to do)")
        print(f"Elapsed: {elapsed:.2f}s")

        await print_stats(mem_ext._storage)

    finally:
        await loader.shutdown()
        await event_bus.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    sys.exit(0)
