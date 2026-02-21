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


def build_consolidator_tools(repo: Any) -> list[Any]:
    """Build consolidator-only tools. Not exposed to Orchestrator."""

    @function_tool(name_override="get_episodes_for_consolidation")
    async def get_episodes_for_consolidation(
        session_id: Annotated[str, Field(description="Session ID to fetch episodes for")],
    ) -> str:
        """Fetch all episodes for a session. Use before extracting facts."""
        episodes = await repo.get_episodes_by_session(session_id)
        if not episodes:
            return "No episodes found for this session."
        lines = [f"[{e['id']}] ({e.get('source_role', '?')}): {e['content']}" for e in episodes]
        return "\n".join(lines)

    @function_tool(name_override="save_fact_with_sources")
    async def save_fact_with_sources(
        content: Annotated[str, Field(min_length=1, description="Fact content")],
        source_ids: Annotated[
            list[str],
            Field(description="Episode IDs that support this fact"),
        ],
        session_id: Annotated[
            str | None,
            Field(default=None, description="Session ID for provenance"),
        ] = None,
        confidence: Annotated[float, Field(default=1.0, ge=0, le=1)] = 1.0,
    ) -> str:
        """Save a fact with provenance. source_ids are episode IDs from get_episodes_for_consolidation."""
        memory_id = await repo.save_fact_with_sources(
            content, source_ids, session_id=session_id, confidence=confidence
        )
        return f"Saved fact (id={memory_id})"

    @function_tool(name_override="mark_session_consolidated")
    async def mark_session_consolidated(
        session_id: Annotated[str, Field(description="Session ID to mark as consolidated")],
    ) -> str:
        """Mark session as consolidated. Call after extraction is complete."""
        await repo.mark_session_consolidated(session_id)
        return f"Session {session_id} marked consolidated."

    @function_tool(name_override="is_session_consolidated")
    async def is_session_consolidated(
        session_id: Annotated[str, Field(description="Session ID to check")],
    ) -> str:
        """Check if session was already consolidated. Skip if true to avoid duplicates."""
        ok = await repo.is_session_consolidated(session_id)
        return "yes" if ok else "no"

    return [
        get_episodes_for_consolidation,
        save_fact_with_sources,
        mark_session_consolidated,
        is_session_consolidated,
    ]
