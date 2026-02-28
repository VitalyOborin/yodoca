"""Test script: invoke Memory v2 hybrid search (FTS5 + vector + graph RRF).

Usage:
    cd yodoca
    python scripts/test_search_memory.py
    python scripts/test_search_memory.py "custom query here"
"""

import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from core import secrets

load_dotenv(_PROJECT_ROOT / ".env")

DEFAULT_QUERIES = [
    "project or task",
    "work preferences",
    "что ты знаешь обо мне",
    "почему мы выбрали такую архитектуру",
]


async def main() -> None:
    from core.events import EventBus
    from core.extensions import Loader, MessageRouter
    from core.llm import ModelRouter
    from core.settings import load_settings

    settings = load_settings()
    model_router = ModelRouter(settings=settings, secrets_getter=secrets.get_secret)
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

    mem_ext = loader._extensions.get("memory")
    if not mem_ext or not mem_ext._retrieval:
        print("ERROR: Memory extension not loaded or retrieval not initialized.")
        await loader.shutdown()
        return

    retrieval = mem_ext._retrieval
    embed_fn = mem_ext._embed_fn

    queries = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_QUERIES

    print("=== Memory v2: Hybrid Search (FTS5 + vector + graph RRF) ===\n")
    for q in queries:
        query_embedding = await embed_fn(q) if embed_fn else None
        results = await retrieval.search(
            q,
            query_embedding=query_embedding,
            limit=5,
            node_types=["episodic", "semantic", "procedural", "opinion"],
        )
        print(f'Query: "{q}" (embedding: {"yes" if query_embedding else "no"})')
        if not results:
            print("  (no results)\n")
            continue
        for r in results:
            preview = (
                r["content"][:100] + "..." if len(r["content"]) > 100 else r["content"]
            )
            conf = r.get("confidence", "?")
            print(f"  [{r['type']}] {r['id'][:8]}.. conf={conf}  {preview}")
        print(f"  total: {len(results)}\n")

    print("=== Context Assembly (as ContextProvider would return) ===\n")
    test_query = queries[0]
    query_embedding = await embed_fn(test_query) if embed_fn else None
    results = await retrieval.search(
        test_query,
        query_embedding=query_embedding,
        limit=10,
    )
    if results:
        context = await retrieval.assemble_context(results, token_budget=2000)
        print(f'Query: "{test_query}"\n')
        print(context or "(empty context)")
    else:
        print(f'Query: "{test_query}" -> no results, no context to assemble')

    print()
    await loader.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
