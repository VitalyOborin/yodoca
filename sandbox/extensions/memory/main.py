"""Memory extension: ToolProvider + ContextProvider. Long-term memory across sessions."""

import logging
import sys
from pathlib import Path
from typing import Any

# Ensure extension dir is on path for sibling imports (db, repository, tools)
_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from db import MemoryDatabase
from repository import MemoryRepository
from tools import build_consolidator_tools, build_tools

logger = logging.getLogger(__name__)


class MemoryExtension:
    """Extension + ToolProvider + ContextProvider: long-term memory with FTS5 search."""

    def __init__(self) -> None:
        self._db: MemoryDatabase | None = None
        self._repo: MemoryRepository | None = None
        self._ctx: Any = None
        self._current_session_id: str | None = None
        self._episodes_per_chunk: int = 30

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
        """Return relevant memory context to prepend to prompt."""
        if not self._repo:
            return None
        results = await self._repo.fts_search(
            prompt,
            limit=5,
            exclude_session_id=self._current_session_id,
        )
        if not results:
            return None
        lines = "\n".join(f"- {r['content']}" for r in results)
        return f"## Relevant memory\n{lines}"

    # --- ToolProvider ---
    def get_tools(self) -> list[Any]:
        if not self._repo:
            return []
        return build_tools(self._repo)

    def get_consolidator_tools(self) -> list[Any]:
        """Tools for consolidator agent only. Not exposed to Orchestrator."""
        if not self._repo:
            return []
        return build_consolidator_tools(self._repo, self._episodes_per_chunk)

    # --- Lifecycle ---
    async def initialize(self, context: Any) -> None:
        self._ctx = context
        self._episodes_per_chunk = context.get_config("episodes_per_chunk", 30)
        db_path = context.data_dir / "memory.db"
        self._db = MemoryDatabase(db_path)
        await self._db.initialize()
        self._repo = MemoryRepository(self._db)
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
        await self._repo.save_episode(
            f"{text}",
            session_id=self._current_session_id,
            source_role="user",
        )

    async def _on_agent_response(self, data: dict[str, Any]) -> None:
        """MessageRouter: agent_response. Save assistant response as episode."""
        if not self._repo:
            return
        text = (data.get("text") or "").strip()
        if not text:
            return
        agent_name = data.get("agent_id") or "orchestrator"
        await self._repo.save_episode(
            f"{text}",
            session_id=self._current_session_id,
            source_role=agent_name,
        )

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
