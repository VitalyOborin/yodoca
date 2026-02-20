"""Agent tools for memory: search, remember, correct, confirm, stats."""

from typing import Annotated, Any

from agents import function_tool
from pydantic import Field


def build_tools(repo: Any) -> list[Any]:
    """Build the 5 memory tools bound to the given repository."""

    @function_tool(name_override="search_memory")
    async def search_memory(
        query: Annotated[str, Field(min_length=1, description="Search query")],
        kind: Annotated[
            str | None,
            Field(default=None, description="Filter by kind: episode, fact, preference, reflection"),
        ] = None,
        tag: Annotated[
            str | None,
            Field(default=None, description="Filter by tag, e.g. work, project_alpha"),
        ] = None,
        limit: Annotated[int, Field(default=10, ge=1, le=50, description="Max results")] = 10,
    ) -> str:
        """Search long-term memory by full-text. Returns relevant memories.

        Use to find past conversations, facts, preferences before answering.
        """
        results = await repo.fts_search(query, kind=kind, tag=tag, limit=limit)
        if not results:
            return "No matching memories found."
        lines = [f"- [{r['kind']}] {r['content']}" for r in results]
        return "\n".join(lines)

    @function_tool(name_override="remember_fact")
    async def remember_fact(
        content: Annotated[str, Field(min_length=1, description="Fact to remember")],
        confidence: Annotated[
            float,
            Field(default=1.0, ge=0, le=1, description="Confidence 0-1"),
        ] = 1.0,
    ) -> str:
        """Explicitly save an important fact to long-term memory."""
        memory_id = await repo.save_fact(content, confidence=confidence)
        return f"Remembered: {content[:80]}... (id={memory_id})"

    @function_tool(name_override="correct_fact")
    async def correct_fact(
        memory_id: Annotated[str, Field(description="ID from search_memory")],
        new_content: Annotated[str, Field(min_length=1, description="Corrected content")],
    ) -> str:
        """Supersede a fact: soft-delete old, create new. Use memory_id from search_memory."""
        ok = await repo.soft_delete(memory_id)
        if not ok:
            return f"Memory {memory_id} not found or already superseded."
        new_id = await repo.save_fact(new_content)
        return f"Corrected. New memory id={new_id}"

    @function_tool(name_override="confirm_fact")
    async def confirm_fact(
        memory_id: Annotated[str, Field(description="ID from search_memory")],
    ) -> str:
        """Mark a fact as protected: permanently in memory, no decay."""
        ok = await repo.update_confidence(memory_id, confidence=1.0, decay_rate=0.0)
        if not ok:
            return f"Memory {memory_id} not found."
        return f"Confirmed memory {memory_id} (protected, no decay)."

    @function_tool(name_override="memory_stats")
    async def memory_stats() -> str:
        """Return counts by kind and last consolidation time."""
        stats = await repo.get_stats()
        counts = stats.get("counts", {})
        latest = stats.get("latest_created_at")
        lines = [f"{k}: {v}" for k, v in sorted(counts.items())]
        if latest:
            from datetime import datetime
            lines.append(f"Latest: {datetime.fromtimestamp(latest).isoformat()}")
        return "\n".join(lines) if lines else "No memories yet."

    return [search_memory, remember_fact, correct_fact, confirm_fact, memory_stats]
