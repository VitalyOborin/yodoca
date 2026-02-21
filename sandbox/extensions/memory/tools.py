"""Agent tools for memory: search, remember, correct, confirm, stats.

All tools return structured output (Pydantic models) for reliable parsing by the LLM.
See: https://openai.github.io/openai-agents-python/tools/
"""

from datetime import datetime
from typing import Annotated, Any

from agents import function_tool
from pydantic import BaseModel, Field


# --- Structured output models (Orchestrator tools) ---


class MemoryItem(BaseModel):
    """A single memory from search results."""

    id: str = Field(description="Memory ID, use for correct_fact/confirm_fact")
    kind: str = Field(description="Memory kind: episode, fact, preference, reflection")
    content: str = Field(description="Memory content")


class SearchMemoryResult(BaseModel):
    """Result of memory search."""

    memories: list[MemoryItem] = Field(default_factory=list, description="Matching memories")
    found: bool = Field(description="True if any memories were found")


class RememberFactResult(BaseModel):
    """Result of saving a fact."""

    memory_id: str = Field(description="ID of the saved memory")
    content_preview: str = Field(description="Short preview of the content")
    success: bool = Field(description="Whether the save succeeded")


class CorrectFactResult(BaseModel):
    """Result of correcting a fact."""

    success: bool = Field(description="Whether the correction succeeded")
    new_memory_id: str | None = Field(default=None, description="ID of the new memory if succeeded")
    message: str = Field(description="Human-readable status message")


class ConfirmFactResult(BaseModel):
    """Result of confirming a fact."""

    success: bool = Field(description="Whether the confirmation succeeded")
    memory_id: str = Field(description="ID of the memory")
    message: str = Field(description="Human-readable status message")


class MemoryStatsResult(BaseModel):
    """Memory statistics."""

    counts: dict[str, int] = Field(default_factory=dict, description="Count per kind")
    latest_created_at: str | None = Field(default=None, description="ISO timestamp of latest memory")


# --- Structured output models (Consolidator tools) ---


class EpisodeItem(BaseModel):
    """A single episode for consolidation."""

    id: str = Field(description="Episode ID")
    content: str = Field(description="Episode content")
    source_role: str | None = Field(default=None, description="Source role: user or agent name")


class GetEpisodesResult(BaseModel):
    """Episodes for a session."""

    episodes: list[EpisodeItem] = Field(default_factory=list, description="Episodes in the session")
    found: bool = Field(description="True if any episodes were found")


class SaveFactWithSourcesResult(BaseModel):
    """Result of saving a fact with provenance."""

    memory_id: str = Field(description="ID of the saved fact")
    success: bool = Field(description="Whether the save succeeded")


class MarkSessionResult(BaseModel):
    """Result of marking session consolidated."""

    success: bool = Field(description="Whether the operation succeeded")
    session_id: str = Field(description="Session ID that was marked")


class IsSessionConsolidatedResult(BaseModel):
    """Result of consolidation check."""

    consolidated: bool = Field(description="True if session was already consolidated")


# --- Tool builders ---


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
    ) -> SearchMemoryResult:
        """Search long-term memory by full-text. Returns relevant memories.

        Use to find past conversations, facts, preferences before answering.
        """
        results = await repo.fts_search(query, kind=kind, tag=tag, limit=limit)
        memories = [
            MemoryItem(id=r["id"], kind=r["kind"], content=r["content"])
            for r in results
        ]
        return SearchMemoryResult(memories=memories, found=len(memories) > 0)

    @function_tool(name_override="remember_fact")
    async def remember_fact(
        content: Annotated[str, Field(min_length=1, description="Fact to remember")],
        confidence: Annotated[
            float,
            Field(default=1.0, ge=0, le=1, description="Confidence 0-1"),
        ] = 1.0,
    ) -> RememberFactResult:
        """Explicitly save an important fact to long-term memory."""
        memory_id = await repo.save_fact(content, confidence=confidence)
        preview = f"{content[:80]}..." if len(content) > 80 else content
        return RememberFactResult(
            memory_id=memory_id,
            content_preview=preview,
            success=True,
        )

    @function_tool(name_override="correct_fact")
    async def correct_fact(
        memory_id: Annotated[str, Field(description="ID from search_memory")],
        new_content: Annotated[str, Field(min_length=1, description="Corrected content")],
    ) -> CorrectFactResult:
        """Supersede a fact: soft-delete old, create new. Use memory_id from search_memory."""
        ok = await repo.soft_delete(memory_id)
        if not ok:
            return CorrectFactResult(
                success=False,
                new_memory_id=None,
                message=f"Memory {memory_id} not found or already superseded.",
            )
        new_id = await repo.save_fact(new_content)
        return CorrectFactResult(
            success=True,
            new_memory_id=new_id,
            message=f"Corrected. New memory id={new_id}",
        )

    @function_tool(name_override="confirm_fact")
    async def confirm_fact(
        memory_id: Annotated[str, Field(description="ID from search_memory")],
    ) -> ConfirmFactResult:
        """Mark a fact as protected: permanently in memory, no decay."""
        ok = await repo.update_confidence(memory_id, confidence=1.0, decay_rate=0.0)
        if not ok:
            return ConfirmFactResult(
                success=False,
                memory_id=memory_id,
                message=f"Memory {memory_id} not found.",
            )
        return ConfirmFactResult(
            success=True,
            memory_id=memory_id,
            message=f"Confirmed memory {memory_id} (protected, no decay).",
        )

    @function_tool(name_override="memory_stats")
    async def memory_stats() -> MemoryStatsResult:
        """Return counts by kind and last consolidation time."""
        stats = await repo.get_stats()
        counts = stats.get("counts", {})
        latest_ts = stats.get("latest_created_at")
        latest_str = (
            datetime.fromtimestamp(latest_ts).isoformat() if latest_ts else None
        )
        return MemoryStatsResult(counts=counts, latest_created_at=latest_str)

    return [search_memory, remember_fact, correct_fact, confirm_fact, memory_stats]


def build_consolidator_tools(repo: Any) -> list[Any]:
    """Build consolidator-only tools. Not exposed to Orchestrator."""

    @function_tool(name_override="get_episodes_for_consolidation")
    async def get_episodes_for_consolidation(
        session_id: Annotated[str, Field(description="Session ID to fetch episodes for")],
    ) -> GetEpisodesResult:
        """Fetch all episodes for a session. Use before extracting facts."""
        episodes = await repo.get_episodes_by_session(session_id)
        items = [
            EpisodeItem(
                id=e["id"],
                content=e["content"],
                source_role=e.get("source_role"),
            )
            for e in episodes
        ]
        return GetEpisodesResult(episodes=items, found=len(items) > 0)

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
    ) -> SaveFactWithSourcesResult:
        """Save a fact with provenance. source_ids are episode IDs from get_episodes_for_consolidation."""
        memory_id = await repo.save_fact_with_sources(
            content, source_ids, session_id=session_id, confidence=confidence
        )
        return SaveFactWithSourcesResult(memory_id=memory_id, success=True)

    @function_tool(name_override="mark_session_consolidated")
    async def mark_session_consolidated(
        session_id: Annotated[str, Field(description="Session ID to mark as consolidated")],
    ) -> MarkSessionResult:
        """Mark session as consolidated. Call after extraction is complete."""
        await repo.mark_session_consolidated(session_id)
        return MarkSessionResult(success=True, session_id=session_id)

    @function_tool(name_override="is_session_consolidated")
    async def is_session_consolidated(
        session_id: Annotated[str, Field(description="Session ID to check")],
    ) -> IsSessionConsolidatedResult:
        """Check if session was already consolidated. Skip if true to avoid duplicates."""
        ok = await repo.is_session_consolidated(session_id)
        return IsSessionConsolidatedResult(consolidated=ok)

    return [
        get_episodes_for_consolidation,
        save_fact_with_sources,
        mark_session_consolidated,
        is_session_consolidated,
    ]
