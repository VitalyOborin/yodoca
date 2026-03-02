"""Memory v3 extension: ToolProvider + ContextProvider + SchedulerProvider."""

import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path

from agents import Agent, Runner
from pydantic import BaseModel

from core.extensions.contract import TurnContext


class EntitySummaryResult(BaseModel):
    """Structured output for entity enrichment."""

    summary: str = ""
from core.events.topics import SystemTopics

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from community import CommunityManager
from decay import DecayService
from pipeline import AtomicWritePipeline
from retrieval import EmbeddingIntentClassifier, HierarchicalRetriever, KeywordIntentClassifier
from storage import MemoryStorage
from tools import build_tools

logger = logging.getLogger(__name__)


class MemoryExtension:
    """Hierarchical knowledge graph. Phase 3: dual-source context, full tools."""

    def __init__(self) -> None:
        self._storage: MemoryStorage | None = None
        self._pipeline: AtomicWritePipeline | None = None
        self._retriever: HierarchicalRetriever | None = None
        self._community_manager: CommunityManager | None = None
        self._decay: DecayService | None = None
        self._ctx: object | None = None
        self._current_session_id: str | None = None
        self._last_consolidation_at: str | None = None
        self._last_decay_at: str | None = None
        self._consolidation_pending: set[str] = set()
        self._embed_fn: object | None = None

    @property
    def context_priority(self) -> int:
        return 50

    async def get_context(
        self,
        prompt: str,
        turn_context: TurnContext,
    ) -> str | None:
        """Dual-source: 70% long-term memory (retriever), 30% current-session episodes."""
        if not self._storage:
            return None

        token_budget = 2000
        long_term_budget = int(token_budget * 0.7)
        session_budget = int(token_budget * 0.3)
        parts: list[str] = []

        # Long-term memory (70%)
        community_summaries: list[dict] = []
        if self._retriever and prompt:
            search_result = await self._retriever.search(
                prompt,
                query_embedding=None,
                limit=15,
                token_budget=long_term_budget,
                return_embedding=True,
            )
            results, query_embedding = search_result
            if query_embedding:
                try:
                    community_summaries = await self._retriever.search_communities(
                        query_embedding, limit=3
                    )
                except Exception:
                    pass
            if results:
                assembled = await self._retriever.assemble_context(
                    results,
                    token_budget=long_term_budget,
                    community_summaries=community_summaries or None,
                )
                if assembled:
                    parts.append("## Long-term memory\n" + assembled)

        # Current-session episodes (30%)
        if self._current_session_id:
            episodes = await self._storage.get_recent_session_episodes(
                self._current_session_id, limit=10
            )
            if episodes:
                lines = [
                    f"- [{ep['actor']}]: {ep['content'][:500]}"
                    for ep in reversed(episodes)
                ]
                parts.append("## Recent conversation\n" + "\n".join(lines))

        return "\n\n".join(parts) if parts else None

    def get_tools(self) -> list:
        if not self._storage:
            return []

        def get_maintenance_info() -> dict:
            return {
                "last_consolidation": self._last_consolidation_at,
                "last_decay_run": self._last_decay_at,
            }

        return build_tools(
            storage=self._storage,
            retriever=self._retriever,
            embed_fn=self._embed_fn,
            pipeline=self._pipeline,
            token_budget=2000,
            get_maintenance_info=get_maintenance_info,
        )

    async def initialize(self, context: object) -> None:
        self._ctx = context
        db_path = context.data_dir / "memory.db"
        embedding_dims = context.get_config("embedding_dimensions", 256)
        self._storage = MemoryStorage(db_path, embedding_dimensions=embedding_dims)
        await self._storage.initialize()

        # Phase 2: AtomicWritePipeline for consolidation
        embed_ext = context.get_extension("embedding")
        embed_fn = embed_ext.embed if embed_ext else None
        embed_batch_fn = embed_ext.embed_batch if embed_ext else None
        self._embed_fn = embed_fn

        model = context.model_router.get_model("memory_agent")
        config = {
            "entity_resolution_threshold": context.get_config("entity_resolution_threshold", 0.85),
            "fact_dedup_threshold": context.get_config("fact_dedup_threshold", 0.90),
            "preferred_predicates": context.get_config("preferred_predicates", []),
            "pipeline_max_attempts": context.get_config("pipeline_max_attempts", 3),
            "community_min_shared_facts": context.get_config("community_min_shared_facts", 2),
        }
        if model:
            self._community_manager = CommunityManager(
                storage=self._storage,
                embed_fn=embed_fn,
                model=model,
                config=config,
                extension_dir=_ext_dir,
            )
            self._pipeline = AtomicWritePipeline(
                storage=self._storage,
                embed_fn=embed_fn,
                embed_batch_fn=embed_batch_fn,
                model=model,
                config=config,
                extension_dir=_ext_dir,
                community_manager=self._community_manager,
            )
        else:
            logger.warning("memory_agent model not configured; pipeline disabled")

        # Phase 3: HierarchicalRetriever for read path
        if embed_fn:
            classifier = EmbeddingIntentClassifier(
                embed_fn=embed_fn,
                embed_batch_fn=embed_batch_fn,
                cache_dir=context.data_dir / "memory_cache",
            )
            await classifier.initialize()
        else:
            classifier = KeywordIntentClassifier()
        retriever_config = {
            "rrf_k": context.get_config("rrf_k", 60),
            "bfs_max_depth": context.get_config("bfs_max_depth", 2),
            "bfs_max_facts": context.get_config("bfs_max_facts", 50),
        }
        self._retriever = HierarchicalRetriever(
            storage=self._storage,
            embed_fn=embed_fn,
            intent_classifier=classifier,
            **retriever_config,
        )

        self._decay = DecayService(
            storage=self._storage,
            lambda_=context.get_config("decay_lambda", 0.1),
            threshold=context.get_config("confidence_threshold", 0.05),
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

    def health_check(self) -> bool:
        return self._storage is not None

    async def execute_task(self, task_name: str) -> dict | None:
        """SchedulerProvider. Phase 1: consolidation tracking only."""
        if task_name == "run_nightly_maintenance":
            if not self._storage:
                return None
            logger.info("Nightly maintenance started")
            unconsolidated = await self._storage.get_unconsolidated_sessions()
            for sid in unconsolidated:
                await self._consolidate_session(sid)
            n_consolidated = len(unconsolidated)

            retried = 0
            if self._pipeline:
                retried = await self._pipeline.retry_failed()

            facts_decayed = 0
            facts_expired = 0
            if self._decay:
                facts_decayed, facts_expired = await self._decay.apply()

            entities_enriched = 0
            if self._pipeline and getattr(self._pipeline, "_model", None):
                entities = await self._storage.get_entities_needing_enrichment(
                    min_mentions=3
                )
                for ent in entities:
                    try:
                        facts = await self._storage.get_facts_for_entity(
                            ent["id"], limit=20
                        )
                        fact_lines = [
                            f.get("fact_text", "")
                            for f in facts
                            if f.get("fact_text")
                        ]
                        if not fact_lines:
                            continue
                        summary = await self._generate_entity_summary(
                            ent["name"], fact_lines
                        )
                        if summary:
                            await self._storage.update_entity(
                                ent["id"], {"summary": summary}
                            )
                            entities_enriched += 1
                    except Exception as e:
                        logger.warning(
                            "Entity enrichment failed for %s: %s", ent["id"], e
                        )

            self._last_consolidation_at = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime()
            )
            self._last_decay_at = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime()
            )
            await self._storage.set_maintenance_timestamps(
                last_consolidation=self._last_consolidation_at,
                last_decay_run=self._last_decay_at,
            )

            summary = (
                f"Nightly: consolidated {n_consolidated} sessions, retried {retried} queue items, "
                f"decayed {facts_decayed} facts, expired {facts_expired} facts, "
                f"enriched {entities_enriched} entities"
            )
            logger.info("Nightly maintenance finished: %s", summary)
            return {"text": summary}
        if task_name == "run_community_refresh":
            if not self._community_manager:
                return {"text": "Community manager not available"}
            logger.info("Community refresh started")
            n = await self._community_manager.periodic_refresh()
            logger.info("Community refresh finished: %d entities processed", n)
            return {"text": f"Community refresh: {n} entities processed"}
        return None

    async def _generate_entity_summary(
        self, name: str, fact_lines: list[str]
    ) -> str | None:
        """Generate a concise entity summary from facts via LLM. Returns summary or None."""
        if not fact_lines or not getattr(self._pipeline, "_model", None):
            return None
        prompt = (
            f"Entity: {name}\n\n"
            "Known facts:\n"
            + "\n".join(f"- {f}" for f in fact_lines[:15] if f.strip())
            + "\n\n"
            "Generate a 1-2 sentence summary of this entity."
        )
        try:
            agent = Agent(
                name="MemoryEntityEnrichmentAgent",
                instructions="Generate a brief entity summary.",
                model=self._pipeline._model,
                output_type=EntitySummaryResult,
            )
            result = await Runner.run(agent, prompt, max_turns=1)
            out = result.final_output
            if isinstance(out, EntitySummaryResult):
                return out.summary.strip() or None
            if isinstance(out, str) and out.strip():
                if out.strip().startswith("{"):
                    data = json.loads(out)
                    return data.get("summary", "").strip() or None
                return out.strip()[:500] or None
        except Exception as e:
            logger.debug("Entity summary generation failed: %s", e)
        return None

    async def _on_user_message(self, data: dict) -> None:
        """Hot path: save user episode to episodes table."""
        if not self._storage:
            return
        text = (data.get("text") or "").strip()
        session_id = data.get("session_id")

        if session_id and session_id != self._current_session_id:
            if self._current_session_id:
                prev_sid = self._current_session_id
                if prev_sid not in self._consolidation_pending:
                    self._consolidation_pending.add(prev_sid)
                    logger.info(
                        "Session switch: scheduling consolidation for %s", prev_sid
                    )
                    task = asyncio.create_task(self._consolidate_session(prev_sid))
                    task.add_done_callback(
                        lambda _: self._consolidation_pending.discard(prev_sid)
                    )
            self._current_session_id = session_id
            self._storage.ensure_session(session_id)
            logger.debug("Active session: %s", session_id)

        if not text:
            return

        now_ms = int(time.time() * 1000)
        episode = {
            "id": str(uuid.uuid4()),
            "content": text,
            "actor": "user",
            "session_id": session_id or "",
            "t_obs": now_ms,
            "created_at": now_ms,
        }
        episode_id = self._storage.insert_episode(episode)
        logger.debug(
            "Episode saved: id=%s session=%s len=%d",
            episode_id[:8],
            session_id,
            len(text),
        )

    async def _on_agent_response(self, data: dict) -> None:
        """Hot path: save agent episode to episodes table."""
        if not self._storage:
            return
        text = (data.get("text") or "").strip()
        if not text:
            return
        session_id = data.get("session_id") or self._current_session_id
        agent_id = data.get("agent_id") or "assistant"

        now_ms = int(time.time() * 1000)
        episode = {
            "id": str(uuid.uuid4()),
            "content": text,
            "actor": agent_id,
            "session_id": session_id or "",
            "t_obs": now_ms,
            "created_at": now_ms,
        }
        episode_id = self._storage.insert_episode(episode)
        logger.debug(
            "Agent episode saved: id=%s agent=%s session=%s len=%d",
            episode_id[:8],
            agent_id,
            session_id,
            len(text),
        )

    async def _on_session_completed(self, event: object) -> None:
        """EventBus: session.completed. Trigger consolidation."""
        payload = getattr(event, "payload", {}) or {}
        session_id = payload.get("session_id")
        if session_id:
            if session_id in self._consolidation_pending:
                logger.debug(
                    "session.completed: consolidation already pending for %s",
                    session_id,
                )
                return
            self._consolidation_pending.add(session_id)
            logger.info(
                "session.completed: scheduling consolidation for %s", session_id
            )
            task = asyncio.create_task(self._consolidate_session(session_id))
            task.add_done_callback(
                lambda _: self._consolidation_pending.discard(session_id)
            )

    async def _consolidate_session(self, session_id: str) -> None:
        """Phase 2: run AtomicWritePipeline, then mark consolidated."""
        try:
            if not self._storage:
                return
            if await self._storage.is_session_consolidated(session_id):
                return
            if self._pipeline:
                result = await self._pipeline.process_session(session_id)
                logger.info(
                    "Session %s: %d episodes, %d facts (Phase 2)",
                    session_id,
                    result.episodes_processed,
                    result.facts_created,
                )
            await self._storage.mark_session_consolidated(session_id)
        except Exception as e:
            logger.exception("consolidate_session failed for %s: %s", session_id, e)
        finally:
            self._consolidation_pending.discard(session_id)
