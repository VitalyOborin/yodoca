"""Agent tools for memory: search, remember, correct, confirm, stats.

All tools return structured output (Pydantic models) for reliable parsing by the LLM.
See: https://openai.github.io/openai-agents-python/tools/
"""

from datetime import datetime
from typing import Annotated, Any, Awaitable, Callable

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
    role: str | None = Field(
        default=None,
        description="Source role: user, orchestrator, or agent_id",
    )
    content: str = Field(description="Episode content")


class GetEpisodesResult(BaseModel):
    """Episodes for a session (paginated)."""

    session_id: str = Field(description="Session ID")
    episodes: list[EpisodeItem] = Field(default_factory=list, description="Episodes in this chunk")
    total: int = Field(ge=0, description="Total episodes in session")
    has_more: bool = Field(default=False, description="True if more chunks available")
    next_offset: int | None = Field(default=None, description="Offset for next chunk if has_more")


class FactInput(BaseModel):
    """Single fact for batch save."""

    content: str = Field(min_length=1, description="Fact as a clear standalone statement")
    source_ids: list[str] = Field(description="Episode IDs supporting this fact")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)


class SavedFactItem(BaseModel):
    """A saved fact from batch."""

    id: str = Field(description="Saved fact ID")
    content_preview: str = Field(description="First 80 chars of content")
    duplicate: bool = Field(default=False, description="True if skipped as duplicate")


class SaveFactsBatchResult(BaseModel):
    """Result of batch fact save."""

    session_id: str = Field(description="Session ID")
    saved: list[SavedFactItem] = Field(default_factory=list)
    saved_count: int = Field(ge=0, description="Number of facts actually saved")
    skipped_duplicates: int = Field(ge=0, description="Duplicates skipped")
    errors: list[str] = Field(default_factory=list, description="Errors for failed saves")


class MarkSessionResult(BaseModel):
    """Result of marking session consolidated."""

    session_id: str = Field(description="Session ID that was marked")
    success: bool = Field(description="Whether the operation succeeded")
    facts_saved: int = Field(
        ge=0,
        description="Number of facts saved for this session during consolidation",
    )


class IsSessionConsolidatedResult(BaseModel):
    """Result of consolidation check."""

    session_id: str = Field(description="Session ID that was checked")
    consolidated: bool = Field(description="True if session was already consolidated")


# --- Tool builders ---

EmbedFn = Callable[[str], Awaitable[list[float] | None]] | None


def build_tools(repo: Any, embed_fn: EmbedFn = None) -> list[Any]:
    """Build the 5 memory tools bound to the given repository and optional embed_fn."""

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
        """Search long-term memory (hybrid FTS5 + semantic). Returns relevant memories.

        Use to find past conversations, facts, preferences before answering.
        """
        query_embedding = await embed_fn(query) if embed_fn else None
        results = await repo.hybrid_search(
            query,
            query_embedding=query_embedding,
            kind=kind,
            tag=tag,
            limit=limit,
        )
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
        if embed_fn:
            embedding = await embed_fn(content)
            if embedding:
                await repo.save_embedding(memory_id, embedding)
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
        if embed_fn:
            embedding = await embed_fn(new_content)
            if embedding:
                await repo.save_embedding(new_id, embedding)
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


def build_consolidator_tools(
    repo: Any, episodes_per_chunk: int = 30, embed_fn: EmbedFn = None
) -> list[Any]:
    """Build consolidator-only tools. Not exposed to Orchestrator."""

    @function_tool(name_override="get_episodes_for_consolidation")
    async def get_episodes_for_consolidation(
        session_id: Annotated[str, Field(description="Session ID to fetch episodes for")],
        offset: Annotated[int, Field(default=0, ge=0, description="Offset for pagination")] = 0,
        limit: Annotated[
            int,
            Field(default=episodes_per_chunk, ge=1, le=100, description="Chunk size"),
        ] = episodes_per_chunk,
    ) -> GetEpisodesResult:
        """Fetch episodes for a session (paginated). Call with offset=next_offset until has_more=false."""
        page, total = await repo.get_episodes_by_session(session_id, offset, limit)
        items = [
            EpisodeItem(
                id=e["id"],
                role=e.get("source_role"),
                content=e["content"],
            )
            for e in page
        ]
        has_more = (offset + len(page)) < total
        next_offset = offset + limit if has_more else None
        return GetEpisodesResult(
            session_id=session_id,
            episodes=items,
            total=total,
            has_more=has_more,
            next_offset=next_offset,
        )

    @function_tool(name_override="save_facts_batch")
    async def save_facts_batch(
        session_id: Annotated[str, Field(description="Session ID for provenance")],
        facts: Annotated[
            list[FactInput],
            Field(default_factory=list, description="Facts to save (may be empty)"),
        ],
    ) -> SaveFactsBatchResult:
        """Save multiple facts in one call. Use after extracting from all episode chunks."""
        batch = [f.model_dump() for f in facts]
        result = await repo.save_facts_batch(session_id, batch)
        if embed_fn:
            for s in result["saved"]:
                content = s.get("content")
                if content:
                    embedding = await embed_fn(content)
                    if embedding:
                        await repo.save_embedding(s["id"], embedding)
        saved_items = [
            SavedFactItem(
                id=s["id"],
                content_preview=s["content_preview"],
                duplicate=s.get("duplicate", False),
            )
            for s in result["saved"]
        ]
        return SaveFactsBatchResult(
            session_id=session_id,
            saved=saved_items,
            saved_count=len(saved_items),
            skipped_duplicates=result["skipped_duplicates"],
            errors=result["errors"],
        )

    @function_tool(name_override="mark_session_consolidated")
    async def mark_session_consolidated(
        session_id: Annotated[str, Field(description="Session ID to mark as consolidated")],
    ) -> MarkSessionResult:
        """Mark session as consolidated. Call after extraction is complete."""
        facts_saved = await repo.count_facts_by_session(session_id)
        await repo.mark_session_consolidated(session_id)
        return MarkSessionResult(
            session_id=session_id,
            success=True,
            facts_saved=facts_saved,
        )

    @function_tool(name_override="is_session_consolidated")
    async def is_session_consolidated(
        session_id: Annotated[str, Field(description="Session ID to check")],
    ) -> IsSessionConsolidatedResult:
        """Check if session was already consolidated. Skip if true to avoid duplicates."""
        ok = await repo.is_session_consolidated(session_id)
        return IsSessionConsolidatedResult(session_id=session_id, consolidated=ok)

    return [
        get_episodes_for_consolidation,
        save_facts_batch,
        mark_session_consolidated,
        is_session_consolidated,
    ]
