"""Memory extension: ToolProvider + ContextProvider. Long-term memory across sessions."""

import logging
import sys
import time
from pathlib import Path
from typing import Any

# Ensure extension dir is on path for sibling imports (db, repository, tools)
_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from db import MemoryDatabase
from entity_linker import extract_and_link
from repository import MemoryRepository
from tools import build_consolidator_tools, build_tools

logger = logging.getLogger(__name__)


class MemoryExtension:
    """Extension + ToolProvider + ContextProvider: long-term memory with hybrid FTS5 + vector search."""

    def __init__(self) -> None:
        self._db: MemoryDatabase | None = None
        self._repo: MemoryRepository | None = None
        self._ctx: Any = None
        self._current_session_id: str | None = None
        self._episodes_per_chunk: int = 30
        self._embed_fn: Any = None
        self._entity_link_fn: Any = None

    # --- ContextProvider ---
    @property
    def context_priority(self) -> int:
        return 100

    async def get_context(
        self,
        prompt: str,
        *,
        agent_id: str | None = None,
    ) -> str | None:
        """Return relevant memory context (hybrid search). Prepends to prompt."""
        if not self._repo:
            return None
        sections: list[str] = []
        query_embedding = None
        if self._embed_fn:
            query_embedding = await self._embed_fn(prompt)
        results = await self._repo.hybrid_search(
            prompt,
            query_embedding=query_embedding,
            kind="fact",
            limit=5,
            exclude_session_id=self._current_session_id,
        )
        if results:
            lines = "\n".join(f"- {r['content']}" for r in results)
            sections.append(f"## Relevant memory\n{lines}")
        reflection = await self._repo.get_latest_reflection()
        if reflection:
            sections.append(f"## Weekly insight\n{reflection['content']}")
        return "\n\n".join(sections) if sections else None

    # --- ToolProvider ---
    def get_tools(self) -> list[Any]:
        if not self._repo:
            return []
        return build_tools(self._repo, self._embed_fn, self._entity_link_fn)

    def get_consolidator_tools(self) -> list[Any]:
        """Tools for consolidator agent only. Not exposed to Orchestrator."""
        if not self._repo:
            return []
        return build_consolidator_tools(
            self._repo,
            self._episodes_per_chunk,
            self._embed_fn,
            self._entity_link_fn,
            conflict_min_confidence=self._ctx.get_config("conflict_min_confidence", 0.8),
        )

    # --- Lifecycle ---
    async def initialize(self, context: Any) -> None:
        self._ctx = context
        self._episodes_per_chunk = context.get_config("episodes_per_chunk", 30)
        db_path = context.data_dir / "memory.db"
        self._db = MemoryDatabase(db_path)
        await self._db.initialize()
        embedding_ext = context.get_extension("embedding")
        if (
            embedding_ext
            and embedding_ext.health_check()
            and self._db.vec_available
        ):
            self._embed_fn = lambda text: embedding_ext.embed(text, dimensions=256)
        else:
            if not embedding_ext or not embedding_ext.health_check():
                logger.warning("embedding extension unavailable, FTS5-only mode")
            elif not self._db.vec_available:
                logger.warning("vec_memories dimension mismatch, FTS5-only mode")
        self._repo = MemoryRepository(self._db)
        ner_ext = context.get_extension("ner")
        if ner_ext and ner_ext.health_check():
            self._entity_link_fn = lambda mid, content: extract_and_link(
                ner_ext, self._repo, mid, content
            )
        else:
            logger.info("ner extension unavailable, entity extraction disabled")
            self._entity_link_fn = None
        context.subscribe("user_message", self._on_user_message)
        context.subscribe("agent_response", self._on_agent_response)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
            self._repo = None

    def health_check(self) -> bool:
        return self._db is not None

    async def _on_user_message(self, data: dict[str, Any]) -> None:
        """MessageRouter: user_message. Save user message as episode."""
        if not self._repo:
            return
        text = (data.get("text") or "").strip()
        session_id = data.get("session_id")

        if session_id and session_id != self._current_session_id:
            self._current_session_id = session_id
            await self._repo.ensure_session(session_id)
            await self._trigger_consolidation(session_id)

        if not text:
            return
        memory_id = await self._repo.save_episode(
            f"{text}",
            session_id=self._current_session_id,
            source_role="user",
        )
        if self._entity_link_fn:
            await self._entity_link_fn(memory_id, text)

    async def _on_agent_response(self, data: dict[str, Any]) -> None:
        """MessageRouter: agent_response. Save assistant response as episode."""
        if not self._repo:
            return
        text = (data.get("text") or "").strip()
        if not text:
            return
        agent_name = data.get("agent_id") or "orchestrator"
        memory_id = await self._repo.save_episode(
            f"{text}",
            session_id=self._current_session_id,
            source_role=agent_name,
        )
        if self._entity_link_fn:
            await self._entity_link_fn(memory_id, text)

    async def _trigger_consolidation(self, current_session_id: str) -> None:
        """Emit memory.session_completed for all pending non-current sessions."""
        pending = await self._repo.get_pending_consolidations(current_session_id)
        for session_id in pending:
            await self._ctx.emit(
                "memory.session_completed",
                {
                    "session_id": session_id,
                    "prompt": f"Consolidate session {session_id}: extract semantic facts.",
                },
            )

    async def get_pending_consolidations(
        self, exclude_session_id: str
    ) -> list[str]:
        """Return session_ids that need consolidation (exclude current)."""
        if not self._repo:
            return []
        return await self._repo.get_pending_consolidations(exclude_session_id)

    async def get_all_pending_consolidations(self) -> list[str]:
        """Return all session_ids that need consolidation (for scheduler)."""
        if not self._repo:
            return []
        return await self._repo.get_all_pending_consolidations()

    async def run_decay_and_prune(self, threshold: float = 0.05) -> dict[str, Any]:
        """Public API: apply Ebbinghaus decay to all active facts.

        Called by memory_maintenance scheduler. Returns stats dict.
        """
        if not self._repo:
            return {
                "decayed": 0,
                "pruned": 0,
                "errors": ["repository not initialized"],
            }
        return await self._repo.apply_decay_and_prune(threshold)

    async def get_recent_memories_for_reflection(
        self, days: int = 7, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Fetch recent facts and episodes for reflection. Called by memory_maintenance."""
        if not self._repo:
            return []
        since_ts = int(time.time()) - (days * 86400)
        return await self._repo.get_recent_memories(
            since_ts=since_ts,
            kinds=["fact", "episode"],
            limit=limit,
        )

    async def save_reflection(
        self,
        content: str,
        source_ids: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Save a reflection. Generates embedding if available. Returns memory id."""
        if not self._repo:
            raise RuntimeError("memory repository not initialized")
        memory_id = await self._repo.save_reflection(
            content=content, source_ids=source_ids or [], tags=tags or []
        )
        if self._embed_fn:
            embedding = await self._embed_fn(content)
            if embedding:
                await self._repo.save_embedding(memory_id, embedding)
        return memory_id

    async def get_latest_reflection_timestamp(self) -> int | None:
        """Return created_at of the most recent reflection, or None. For idempotency."""
        if not self._repo:
            return None
        return await self._repo.get_latest_reflection_timestamp()
