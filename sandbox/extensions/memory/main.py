"""Memory v2 extension: ToolProvider + ContextProvider + SchedulerProvider."""

import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path

from core.extensions.contract import TurnContext
from core.events.topics import SystemTopics

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from agents import ModelSettings

from agent import create_memory_agent
from agent_tools import build_write_path_tools
from decay import DecayService
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


def _build_embed_fns(embedding_ext: object, dims: int) -> tuple[object | None, object | None]:
    """Build embed_fn and embed_batch_fn from embedding extension. Returns (embed_fn, embed_batch_fn)."""
    embed_fn = lambda text: embedding_ext.embed(text, dimensions=dims)
    embed_batch_fn = (
        (lambda texts: embedding_ext.embed_batch(texts, dimensions=dims))
        if hasattr(embedding_ext, "embed_batch")
        else None
    )
    return embed_fn, embed_batch_fn


class MemoryExtension:
    """Graph-based cognitive memory. Phase 1: episodes, FTS5, temporal edges."""

    def __init__(self) -> None:
        self._storage: MemoryStorage | None = None
        self._retrieval: MemoryRetrieval | None = None
        self._embed_fn: object | None = None
        self._write_agent: object | None = None
        self._decay_service: DecayService | None = None
        self._ctx: object | None = None
        self._current_session_id: str | None = None
        self._token_budget: int = 2000
        self._dedup_threshold: float = 0.92
        self._last_consolidation_at: str | None = None
        self._last_decay_at: str | None = None
        self._consolidation_pending: set[str] = set()

    @property
    def context_priority(self) -> int:
        return 50

    async def get_context(
        self,
        prompt: str,
        turn_context: TurnContext,
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
            graph_depth=params.get("graph_depth"),
        )
        if not results:
            logger.debug("get_context: no results for %r (complexity=%s)", prompt[:80], complexity)
            return None
        context = await self._retrieval.assemble_context(
            results,
            token_budget=params["token_budget"],
        )
        logger.debug(
            "get_context: %d results, %d chars (complexity=%s, agent=%s)",
            len(results), len(context or ""), complexity, turn_context.agent_id,
        )
        return context

    def get_tools(self) -> list:
        if not self._retrieval or not self._storage:
            return []
        def get_maintenance_info() -> dict:
            return {
                "last_consolidation": self._last_consolidation_at,
                "last_decay_run": self._last_decay_at,
            }
        return build_tools(
            retrieval=self._retrieval,
            storage=self._storage,
            embed_fn=self._embed_fn,
            token_budget=self._token_budget,
            get_maintenance_info=get_maintenance_info,
            dedup_threshold=self._dedup_threshold,
        )

    async def initialize(self, context: object) -> None:
        self._ctx = context
        self._token_budget = context.get_config("context_token_budget", 2000)
        self._dedup_threshold = context.get_config("remember_fact_dedup_threshold", 0.92)
        db_path = context.data_dir / "memory.db"
        self._storage = MemoryStorage(db_path)
        await self._storage.initialize()

        embedding_ext = context.get_extension("embedding")
        self._embed_fn = None
        embed_batch_fn = None
        if embedding_ext and embedding_ext.health_check():
            dims = context.get_config("embedding_dimensions", 256)
            self._embed_fn, embed_batch_fn = _build_embed_fns(embedding_ext, dims)

        if self._embed_fn:
            classifier = EmbeddingIntentClassifier(
                embed_fn=self._embed_fn,
                threshold=context.get_config("intent_similarity_threshold", 0.45),
                embed_batch_fn=embed_batch_fn,
                cache_dir=context.data_dir,
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
            rrf_weight_graph=context.get_config("rrf_weight_graph", 1.0),
        )

        self._write_agent = None
        if context.model_router:
            try:
                model = context.model_router.get_model("memory_agent")
                write_tools = build_write_path_tools(
                    storage=self._storage,
                    retrieval=self._retrieval,
                    embed_fn=self._embed_fn,
                    embed_batch_fn=embed_batch_fn,
                )
                self._write_agent = create_memory_agent(
                    model=model,
                    tools=write_tools,
                    extension_dir=_ext_dir,
                    model_settings=ModelSettings(parallel_tool_calls=True),
                )
                logger.info("Write-path agent initialized (model=%s)", model)
            except Exception as e:
                logger.warning("Write-path agent unavailable: %s", e)
        self._decay_service = DecayService(
            decay_threshold=context.get_config("decay_threshold", 0.05),
        )

        prev_session = await self._storage.get_latest_session_id()
        if prev_session:
            self._current_session_id = prev_session
            logger.info("Resumed previous session: %s", prev_session)

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
        self._write_agent = None
        self._decay_service = None

    def health_check(self) -> bool:
        return self._storage is not None

    async def execute_task(self, task_name: str) -> dict | None:
        """SchedulerProvider. Nightly maintenance: consolidate, decay, enrich, causal."""
        if task_name == "run_nightly_maintenance":
            if not self._storage:
                return None
            logger.info("Nightly maintenance started")
            unconsolidated = await self._storage.get_unconsolidated_sessions()
            for sid in unconsolidated:
                await self._consolidate_session(sid)
            n_consolidated = len(unconsolidated)

            decay_stats = {"decayed": 0, "pruned": 0}
            if self._decay_service:
                decay_stats = await self._decay_service.apply(self._storage)
                self._last_decay_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

            self._last_consolidation_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            await self._storage.set_maintenance_timestamps(
                last_consolidation=self._last_consolidation_at,
                last_decay_run=self._last_decay_at,
            )

            enrichment_count = await self._enrich_entities()
            causal_count = await self._infer_causal_edges()

            summary = (
                f"Nightly: consolidated {n_consolidated}, "
                f"decayed {decay_stats['decayed']} pruned {decay_stats['pruned']}, "
                f"enriched {enrichment_count}, causal_pairs_analyzed {causal_count}"
            )
            logger.info("Nightly maintenance finished: %s", summary)
            return {"text": summary}
        return None

    async def _enrich_entities(self) -> int:
        """Enrich entities with sparse summaries. Returns count enriched."""
        if not self._write_agent or not self._storage or not self._ctx:
            return 0
        min_mentions = self._ctx.get_config("entity_enrichment_min_mentions", 3)
        entities = await self._storage.get_entities_needing_enrichment(
            min_mentions=min_mentions
        )
        count = 0
        for ent in entities[:5]:
            nodes = await self._storage.entity_nodes_for_entity(
                ent["id"], node_types=["semantic", "procedural", "opinion"], limit=10
            )
            contents = [n.get("content", "") for n in nodes if n.get("content")]
            if contents and await self._write_agent.enrich_entity(
                ent["id"],
                ent["canonical_name"],
                ent["type"],
                contents,
            ):
                count += 1
        return count

    async def _infer_causal_edges(self) -> int:
        """Infer causal edges between consecutive episodes. Returns count of pairs analyzed."""
        if not self._write_agent or not self._storage or not self._ctx:
            return 0
        batch_size = self._ctx.get_config("causal_inference_batch_size", 50)
        pairs = await self._storage.get_consecutive_episode_pairs(limit=batch_size)
        if not pairs:
            return 0
        return await self._write_agent.infer_causal_edges(pairs)

    async def _on_user_message(self, data: dict) -> None:
        """Hot path: save user episode, temporal edge."""
        if not self._storage:
            return
        text = (data.get("text") or "").strip()
        session_id = data.get("session_id")

        if session_id and session_id != self._current_session_id:
            if self._current_session_id:
                prev_sid = self._current_session_id
                if prev_sid not in self._consolidation_pending:
                    self._consolidation_pending.add(prev_sid)
                    logger.info("Session switch: scheduling consolidation for %s", prev_sid)
                    task = asyncio.create_task(self._consolidate_session(prev_sid))
                    task.add_done_callback(
                        lambda _: self._consolidation_pending.discard(prev_sid)
                    )
            self._current_session_id = session_id
            self._storage.ensure_session(session_id)
            logger.debug("Active session: %s", session_id)

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
        logger.debug("Episode saved: node=%s session=%s len=%d", node_id[:8], session_id, len(text))
        if self._embed_fn:
            asyncio.create_task(self._slow_path(node_id, text))

    async def _slow_path(self, node_id: str, content: str) -> None:
        """Generate embedding for episodic node and save to vec_nodes."""
        if not self._embed_fn or not self._storage:
            return
        for attempt in range(3):
            try:
                embedding = await self._embed_fn(content)
                if embedding:
                    await self._storage.save_embedding(node_id, embedding)
                return
            except Exception:
                if attempt == 2:
                    logger.exception(
                        "Slow-path embedding failed (all retries) for node %s",
                        node_id[:8],
                    )
                else:
                    await asyncio.sleep(2**attempt)

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
        logger.debug("Agent episode saved: node=%s agent=%s session=%s len=%d", node_id[:8], agent_id, session_id, len(text))
        if self._embed_fn:
            asyncio.create_task(self._slow_path(node_id, text))

    async def _on_session_completed(self, event: object) -> None:
        """EventBus: session.completed. Trigger consolidation."""
        payload = getattr(event, "payload", {}) or {}
        session_id = payload.get("session_id")
        if session_id:
            if session_id in self._consolidation_pending:
                logger.debug("session.completed: consolidation already pending for %s", session_id)
                return
            self._consolidation_pending.add(session_id)
            logger.info("session.completed: scheduling consolidation for %s", session_id)
            task = asyncio.create_task(self._consolidate_session(session_id))
            task.add_done_callback(
                lambda _: self._consolidation_pending.discard(session_id)
            )

    async def _consolidate_session(self, session_id: str) -> None:
        """Consolidate session via write-path agent."""
        try:
            if not self._write_agent or not self._storage:
                logger.info("consolidate_session skipped (no write agent): %s", session_id)
                return
            if await self._storage.is_session_consolidated(session_id):
                return
            result = await self._write_agent.consolidate_session(session_id)
            logger.info("Session %s consolidated: %s", session_id, result)
        except Exception as e:
            logger.exception("consolidate_session failed for %s: %s", session_id, e)
        finally:
            self._consolidation_pending.discard(session_id)
