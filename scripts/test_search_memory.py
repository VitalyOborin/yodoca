"""Test script: invoke search_memory to verify semantic search works."""

import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Fix Windows console Unicode
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")


async def main() -> None:
    from core.settings import load_settings
    from core.llm import ModelRouter
    from core.extensions import Loader, MessageRouter
    from core.events import EventBus

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

    await loader.discover()
    await loader.load_all()
    await loader.initialize_all(router)
    loader.detect_and_wire_all(router)

    # Get memory extension and call hybrid_search directly (same logic as search_memory tool)
    mem_ext = loader._extensions.get("memory")
    if not mem_ext or not mem_ext._repo:
        print("Memory extension not loaded")
        return

    repo = mem_ext._repo
    embed_fn = mem_ext._embed_fn

    # Test queries: semantic (concept-based) and lexical (exact words)
    queries = [
        "project or task",
        "work preferences",
        "user likes or dislikes",
        "meeting or schedule",
    ]
    print("=== Testing search_memory / hybrid_search (FTS5 + vector + entity) ===\n")
    for q in queries:
        query_embedding = await embed_fn(q) if embed_fn else None
        results = await repo.hybrid_search(
            q, query_embedding=query_embedding, kind="fact", limit=3
        )
        print(f'Query: "{q}" (embedding: {"yes" if query_embedding else "no"})')
        for r in results:
            preview = r["content"][:80] + "..." if len(r["content"]) > 80 else r["content"]
            print(f"  [{r['kind']}] {r['id']}: {preview}")
        print(f"  count={len(results)}\n")


if __name__ == "__main__":
    asyncio.run(main())
