"""Memory extension: ToolProvider + ContextProvider. Long-term memory across sessions."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Ensure extension dir is on path for sibling imports (db, repository, tools)
_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from db import MemoryDatabase
from repository import MemoryRepository
from tools import build_tools

if TYPE_CHECKING:
    from core.events.models import Event


class MemoryExtension:
    """Extension + ToolProvider + ContextProvider: long-term memory with FTS5 search."""

    def __init__(self) -> None:
        self._db: MemoryDatabase | None = None
        self._repo: MemoryRepository | None = None

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
        results = await self._repo.fts_search(prompt, limit=5)
        if not results:
            return None
        lines = "\n".join(f"- {r['content']}" for r in results)
        return f"## Relevant memory\n{lines}"

    # --- ToolProvider ---
    def get_tools(self) -> list[Any]:
        if not self._repo:
            return []
        return build_tools(self._repo)

    # --- Lifecycle ---
    async def initialize(self, context: Any) -> None:
        db_path = context.data_dir / "memory.db"
        self._db = MemoryDatabase(db_path)
        await self._db.initialize()
        self._repo = MemoryRepository(self._db)
        context.subscribe_event("user.message", self._on_user_message)
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

    async def _on_user_message(self, event: "Event") -> None:
        """EventBus: user.message. Save user message as episode."""
        if not self._repo:
            return
        text = (event.payload.get("text") or "").strip()
        if not text:
            return
        content = f"User: {text}"
        await self._repo.save_episode(content)

    async def _on_agent_response(self, data: dict[str, Any]) -> None:
        """MessageRouter: agent_response. Save assistant response as episode."""
        if not self._repo:
            return
        text = (data.get("text") or "").strip()
        if not text:
            return
        content = f"Assistant: {text}"
        await self._repo.save_episode(content)
