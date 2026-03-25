"""Memory v2 extension: ToolProvider + ContextProvider + SchedulerProvider."""

import asyncio
import logging
import time
import uuid

from agents import ModelSettings
from pydantic import BaseModel, ConfigDict

from core.events.topics import SystemTopics
from core.extensions.contract import TurnContext
from core.llm.capabilities import EmbeddingCapability
from sandbox.extensions.memory.agent import create_memory_agent
from sandbox.extensions.memory.agent_tools import build_write_path_tools
from sandbox.extensions.memory.decay import DecayService
from sandbox.extensions.memory.retrieval import (
    EmbeddingIntentClassifier,
    KeywordIntentClassifier,
    MemoryRetrieval,
    classify_query_complexity,
    get_adaptive_params,
)
from sandbox.extensions.memory.storage import MemoryStorage
from sandbox.extensions.memory.tools import build_tools

logger = logging.getLogger(__name__)


class MemoryExtensionConfig(BaseModel):
    """Merged manifest config + settings.extensions.memory overrides."""

    model_config = ConfigDict(extra="forbid")

    embedding_model: str = "text-embedding-3-large"
    decay_threshold: float = 0.05
    decay_rate_default: float = 0.1
    entity_enrichment_min_mentions: int = 3
    causal_inference_batch_size: int = 50
    consolidation_episodes_per_chunk: int = 30
    conflict_min_confidence: float = 0.8
    context_token_budget: int = 2000
    rrf_k: int = 60
    rrf_weight_vector: float = 1.0
    rrf_weight_fts: float = 1.0
    rrf_weight_graph: float = 1.0
    rrf_min_score_ratio: float = 0.4
    intent_similarity_threshold: float = 0.45
    remember_fact_dedup_threshold: float = 0.92


def _build_embed_fns_from_capability(
    embedder: EmbeddingCapability,
    model: str,
) -> tuple[object, object]:
    """Build embed_fn and embed_batch_fn from EmbeddingCapability."""

    async def embed_fn(text: str) -> list[float] | None:
        results = await embedder.embed_batch([text], model=model)
        return results[0] if results else None

    async def embed_batch_fn(texts: list[str]) -> list[list[float] | None]:
        return await embedder.embed_batch(texts, model=model)

    return embed_fn, embed_batch_fn


async def _probe_embedding_dimension(
    embedder: EmbeddingCapability | None,
    model: str,
) -> int | None:
    """Probe the model once to determine its native embedding dimension."""
    if embedder is None:
        return None
    try:
        results = await embedder.embed_batch(["dimension probe"], model=model)
    except Exception as e:
        logger.warning("Embedding dimension probe failed for model %s: %s", model, e)
        return None

    if not results or not results[0]:
        logger.warning("Embedding dimension probe returned no data for model %s", model)
        return None
    return len(results[0])


class MemoryExtension:
    """Graph-based cognitive memory. Phase 1: episodes, FTS5, temporal edges."""

    ConfigModel = MemoryExtensionConfig

    def __init__(self) -> None:
        self._storage: MemoryStorage | None = None
        self._retrieval: MemoryRetrieval | None = None
        self._embed_fn: object | None = None
        self._write_agent: object | None = None
        self._decay_service: DecayService | None = None
        self._ctx: object | None = None
        self._current_thread_id: str | None = None
        self._token_budget: int = 2000
        self._dedup_threshold: float = 0.92
        self._last_consolidation_at: str | None = None
        self._last_decay_at: str | None = None
        self._consolidation_pending: set[str] = set()
        self._last_episode_id: str | None = None

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
            enrich_provenance=(complexity == "complex"),
        )
        if not results:
            logger.debug(
                "get_context: no results for %r (complexity=%s)",
                prompt[:80],
                complexity,
            )
            return None
        context = await self._retrieval.assemble_context(
            results,
            token_budget=params["token_budget"],
        )
        logger.debug(
            "get_context: %d results, %d chars (complexity=%s, agent=%s)",
            len(results),
            len(context or ""),
            complexity,
            turn_context.agent_id,
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
            get_last_episode_id=lambda: self._last_episode_id,
        )

    async def initialize(self, context: object) -> None:
        self._ctx = context
        self._token_budget = context.get_config("context_token_budget", 2000)
        self._dedup_threshold = context.get_config(
            "remember_fact_dedup_threshold", 0.92
        )
        db_path = context.data_dir / "memory.db"
        self._storage = MemoryStorage(db_path)
        await self._storage.initialize()

        embedding_model = context.get_config(
            "embedding_model", "text-embedding-3-large"
        )
        embedder = (
            context.model_router.get_capability(EmbeddingCapability)
            if context.model_router
            else None
        )
        self._embed_fn = None
        embed_batch_fn = None
        active_embedding_dim = await self._storage.ensure_vec_tables(
            await _probe_embedding_dimension(embedder, embedding_model)
        )
        if active_embedding_dim is not None:
            logger.info(
                "Memory embedding dimension active: %d (model=%s)",
                active_embedding_dim,
                embedding_model,
            )
        else:
            logger.info(
                "Memory vector search disabled: no active embedding dimension "
                "(model=%s)",
                embedding_model,
            )
        if embedder:
            self._embed_fn, embed_batch_fn = _build_embed_fns_from_capability(
                embedder, embedding_model
            )
        if active_embedding_dim is None:
            self._embed_fn = None
            embed_batch_fn = None

        if self._embed_fn and active_embedding_dim is not None:
            classifier = EmbeddingIntentClassifier(
                embed_fn=self._embed_fn,
                threshold=context.get_config("intent_similarity_threshold", 0.45),
                embed_batch_fn=embed_batch_fn,
                cache_dir=context.data_dir,
                model_name=embedding_model,
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
            min_score_ratio=context.get_config("rrf_min_score_ratio", 0.4),
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
                    extension_dir=context.extension_dir,
                    model_settings=ModelSettings(parallel_tool_calls=True),
                )
                logger.info("Write-path agent initialized (model=%s)", model)
            except Exception as e:
                logger.warning("Write-path agent unavailable: %s", e)
        self._decay_service = DecayService(
            decay_threshold=context.get_config("decay_threshold", 0.05),
        )

        prev_thread_id = await self._storage.get_latest_thread_id()
        if prev_thread_id:
            self._current_thread_id = prev_thread_id
            logger.info("Resumed previous thread: %s", prev_thread_id)

        context.subscribe("user_message", self._on_user_message)
        context.subscribe("agent_response", self._on_agent_response)
        context.subscribe_event(
            SystemTopics.THREAD_COMPLETED,
            self._on_thread_completed,
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
            unconsolidated = await self._storage.get_unconsolidated_threads()
            for thread_id in unconsolidated:
                await self._consolidate_thread(thread_id)
            n_consolidated = len(unconsolidated)

            decay_stats = {"decayed": 0, "pruned": 0}
            if self._decay_service:
                decay_stats = await self._decay_service.apply(self._storage)
                self._last_decay_at = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime()
                )

            self._last_consolidation_at = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime()
            )
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
        thread_id = data.get("thread_id")

        if thread_id and thread_id != self._current_thread_id:
            if self._current_thread_id:
                prev_thread_id = self._current_thread_id
                if prev_thread_id not in self._consolidation_pending:
                    self._consolidation_pending.add(prev_thread_id)
                    logger.info(
                        "Thread switch: scheduling consolidation for %s",
                        prev_thread_id,
                    )
                    task = asyncio.create_task(self._consolidate_thread(prev_thread_id))
                    task.add_done_callback(
                        lambda _, tid=prev_thread_id: (
                            self._consolidation_pending.discard(tid)
                        )
                    )
            self._current_thread_id = thread_id
            self._storage.ensure_thread(thread_id)
            logger.debug("Active thread: %s", thread_id)

        if not text:
            return

        prev_id = await self._storage.get_last_episode_id(thread_id or "")
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
            "thread_id": thread_id,
        }
        self._storage.insert_node(node)
        if prev_id:
            self._storage.insert_edge(
                {
                    "source_id": prev_id,
                    "target_id": node_id,
                    "relation_type": "temporal",
                    "valid_from": now,
                    "created_at": now,
                }
            )
        self._last_episode_id = node_id
        logger.debug(
            "Episode saved: node=%s thread=%s len=%d",
            node_id[:8],
            thread_id,
            len(text),
        )
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
        thread_id = data.get("thread_id") or self._current_thread_id
        agent_id = data.get("agent_id") or "orchestrator"

        prev_id = await self._storage.get_last_episode_id(thread_id or "")
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
            "thread_id": thread_id,
        }
        self._storage.insert_node(node)
        if prev_id:
            self._storage.insert_edge(
                {
                    "source_id": prev_id,
                    "target_id": node_id,
                    "relation_type": "temporal",
                    "valid_from": now,
                    "created_at": now,
                }
            )
        self._last_episode_id = node_id
        logger.debug(
            "Agent episode saved: node=%s agent=%s thread=%s len=%d",
            node_id[:8],
            agent_id,
            thread_id,
            len(text),
        )
        if self._embed_fn:
            asyncio.create_task(self._slow_path(node_id, text))

    async def _on_thread_completed(self, event: object) -> None:
        """EventBus: session.completed with thread payload. Trigger consolidation."""
        payload = getattr(event, "payload", {}) or {}
        thread_id = payload.get("thread_id")
        if thread_id:
            if thread_id in self._consolidation_pending:
                logger.debug(
                    "session.completed(thread): consolidation already pending for %s",
                    thread_id,
                )
                return
            self._consolidation_pending.add(thread_id)
            logger.info(
                "session.completed(thread): scheduling consolidation for %s", thread_id
            )
            task = asyncio.create_task(self._consolidate_thread(thread_id))
            task.add_done_callback(
                lambda _: self._consolidation_pending.discard(thread_id)
            )

    async def _consolidate_thread(self, thread_id: str) -> None:
        """Consolidate thread via write-path agent."""
        try:
            if not self._write_agent or not self._storage:
                logger.info(
                    "consolidate_thread skipped (no write agent): %s", thread_id
                )
                return
            if await self._storage.is_thread_consolidated(thread_id):
                return
            result = await self._write_agent.consolidate_thread(thread_id)
            logger.info("Thread %s consolidated: %s", thread_id, result)
        except Exception as e:
            logger.exception("consolidate_thread failed for %s: %s", thread_id, e)
        finally:
            self._consolidation_pending.discard(thread_id)
