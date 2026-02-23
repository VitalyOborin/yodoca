"""Memory v2 extension: ToolProvider + ContextProvider + SchedulerProvider."""

import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path

from core.events.topics import SystemTopics

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from retrieval import (
    EmbeddingIntentClassifier,
    KeywordIntentClassifier,
    MemoryRetrieval,
    classify_query_complexity,
    get_adaptive_params,
)
from storage import MemoryStorage
from tools import build_tools

logger = logging.getLogger(__name__)


class MemoryExtension:
    """Graph-based cognitive memory. Phase 1: episodes, FTS5, temporal edges."""

    def __init__(self) -> None:
        self._storage: MemoryStorage | None = None
        self._retrieval: MemoryRetrieval | None = None
        self._embed_fn: object | None = None
        self._ctx: object | None = None
        self._current_session_id: str | None = None
        self._token_budget: int = 2000

    @property
    def context_priority(self) -> int:
        return 50

    async def get_context(
        self,
        prompt: str,
        *,
        agent_id: str | None = None,
    ) -> str | None:
        """Return relevant memory context. Hybrid FTS5 + vector with RRF."""
        if not self._retrieval:
            return None
        complexity = classify_query_complexity(prompt)
        params = get_adaptive_params(complexity)
        query_embedding = None
        if self._embed_fn:
            query_embedding = await self._embed_fn(prompt)
        results = await self._retrieval.search(
            prompt,
            query_embedding=query_embedding,
            limit=params["limit"],
            token_budget=params["token_budget"],
        )
        if not results:
            return None
        return self._retrieval.assemble_context(
            results,
            token_budget=params["token_budget"],
        )

    def get_tools(self) -> list:
        if not self._retrieval or not self._storage:
            return []
        return build_tools(
            retrieval=self._retrieval,
            storage=self._storage,
            embed_fn=self._embed_fn,
            token_budget=self._token_budget,
        )

    async def initialize(self, context: object) -> None:
        self._ctx = context
        self._token_budget = context.get_config("context_token_budget", 2000)
        db_path = context.data_dir / "memory.db"
        self._storage = MemoryStorage(db_path)
        await self._storage.initialize()

        embedding_ext = context.get_extension("embedding")
        self._embed_fn = None
        if embedding_ext and embedding_ext.health_check():
            dims = context.get_config("embedding_dimensions", 256)
            self._embed_fn = lambda text: embedding_ext.embed(text, dimensions=dims)

        if self._embed_fn:
            classifier = EmbeddingIntentClassifier(
                embed_fn=self._embed_fn,
                threshold=context.get_config("intent_similarity_threshold", 0.45),
            )
            await classifier.initialize()
        else:
            classifier = KeywordIntentClassifier()

        self._retrieval = MemoryRetrieval(
            storage=self._storage,
            intent_classifier=classifier,
            rrf_k=context.get_config("rrf_k", 60),
            rrf_weight_fts=context.get_config("rrf_weight_fts", 1.0),
            rrf_weight_vector=context.get_config("rrf_weight_vector", 1.0),
        )

        context.subscribe("user_message", self._on_user_message)
        context.subscribe("agent_response", self._on_agent_response)
        context.subscribe_event(
            SystemTopics.SESSION_COMPLETED,
            self._on_session_completed,
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        if self._storage:
            await self._storage.close()
            self._storage = None
            self._retrieval = None

    def health_check(self) -> bool:
        return self._storage is not None

    async def execute_task(self, task_name: str) -> dict | None:
        """SchedulerProvider. Phase 1: stub."""
        if task_name == "run_nightly_maintenance":
            logger.info("nightly_maintenance not yet implemented (Phase 3+5)")
            return None
        return None

    async def _on_user_message(self, data: dict) -> None:
        """Hot path: save user episode, temporal edge."""
        if not self._storage:
            return
        text = (data.get("text") or "").strip()
        session_id = data.get("session_id")

        if session_id and session_id != self._current_session_id:
            if self._current_session_id:
                asyncio.create_task(self._consolidate_session(self._current_session_id))
            self._current_session_id = session_id
            self._storage.ensure_session(session_id)

        if not text:
            return

        prev_id = await self._storage.get_last_episode_id(session_id or "")
        now = int(time.time())
        node_id = str(uuid.uuid4())
        node = {
            "id": node_id,
            "type": "episodic",
            "content": text,
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "source_type": "conversation",
            "source_role": "user",
            "session_id": session_id,
        }
        self._storage.insert_node(node)
        if prev_id:
            self._storage.insert_edge({
                "source_id": prev_id,
                "target_id": node_id,
                "relation_type": "temporal",
                "valid_from": now,
                "created_at": now,
            })
        if self._embed_fn:
            asyncio.create_task(self._slow_path(node_id, text))

    async def _slow_path(self, node_id: str, content: str) -> None:
        """Generate embedding for episodic node and save to vec_nodes."""
        if not self._embed_fn or not self._storage:
            return
        embedding = await self._embed_fn(content)
        if embedding:
            await self._storage.save_embedding(node_id, embedding)

    async def _on_agent_response(self, data: dict) -> None:
        """Hot path: save agent episode, temporal edge."""
        if not self._storage:
            return
        text = (data.get("text") or "").strip()
        if not text:
            return
        session_id = data.get("session_id") or self._current_session_id
        agent_id = data.get("agent_id") or "orchestrator"

        prev_id = await self._storage.get_last_episode_id(session_id or "")
        now = int(time.time())
        node_id = str(uuid.uuid4())
        node = {
            "id": node_id,
            "type": "episodic",
            "content": text,
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "source_type": "conversation",
            "source_role": agent_id,
            "session_id": session_id,
        }
        self._storage.insert_node(node)
        if prev_id:
            self._storage.insert_edge({
                "source_id": prev_id,
                "target_id": node_id,
                "relation_type": "temporal",
                "valid_from": now,
                "created_at": now,
            })
        if self._embed_fn:
            asyncio.create_task(self._slow_path(node_id, text))

    async def _on_session_completed(self, event: object) -> None:
        """EventBus: session.completed. Trigger consolidation."""
        payload = getattr(event, "payload", {}) or {}
        session_id = payload.get("session_id")
        if session_id:
            asyncio.create_task(self._consolidate_session(session_id))

    async def _consolidate_session(self, session_id: str) -> None:
        """Consolidate session. Phase 1: stub."""
        logger.info("consolidate_session stub for %s (Phase 3)", session_id)
