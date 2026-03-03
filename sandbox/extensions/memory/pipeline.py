"""AtomicWritePipeline: post-session atomic decomposition and structured fact extraction. Memory v3 Phase 2."""

import asyncio
import json
import logging
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agents import Agent, ModelSettings, Runner
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0 if either is empty or zero-norm."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


# --- Pydantic models for LLM structured output ---


class DecompositionResult(BaseModel):
    """Result of atomic decomposition. Module 1."""

    facts: list[str] = []


class ExtractedEntity(BaseModel):
    """Subject or object in extracted triple."""

    name: str
    entity_type: str = ""


class ExtractionResult(BaseModel):
    """Result of entity + fact extraction. Modules 2+3."""

    subject: ExtractedEntity
    predicate: str
    object: ExtractedEntity
    fact_text: str
    t_valid: int | None = None
    t_invalid: int | None = None


@dataclass
class PipelineResult:
    """Aggregate result of process_session."""

    session_id: str
    episodes_processed: int = 0
    facts_created: int = 0
    entities_created: int = 0
    queue_items_processed: int = 0
    errors: list[str] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class EntityResolver:
    """Multi-tier entity resolution. Tiers 1-3 (Tier 4 LLM deferred)."""

    def __init__(
        self,
        storage: Any,
        embed_fn: Callable[[str], Any] | None,
        threshold: float = 0.85,
    ) -> None:
        self._storage = storage
        self._embed_fn = embed_fn
        self._threshold = threshold

    async def resolve(self, name: str, embedding: list[float] | None) -> tuple[str, bool]:
        """Returns (entity_id, is_new). Creates entity if no match."""
        name = (name or "").strip()
        if not name:
            return "", False

        # Tier 1: exact normalized name match
        ent = await self._storage.get_entity_by_normalized_name(name)
        if ent:
            return ent["id"], False

        # Tier 2: alias match
        ent = await self._storage.get_entity_by_alias(name)
        if ent:
            return ent["id"], False

        # Tier 3: vector similarity
        if embedding and self._embed_fn:
            candidates = await self._storage.vec_search_entities(embedding, top_k=5)
            for c in candidates:
                dist = c.get("distance", float("inf"))
                sim = max(0.0, 1.0 - (dist * dist) / 2.0)  # L2 -> cosine for unit vectors
                if sim >= self._threshold:
                    return c["entity_id"], False

        # None: create new entity
        entity_id = str(uuid.uuid4())
        entity = {
            "id": entity_id,
            "name": name,
            "entity_type": "",
            "aliases": [],
            "mention_count": 1,
        }
        await self._storage.insert_entity_awaitable(entity)
        return entity_id, True


class AtomicWritePipeline:
    """Post-session pipeline: decompose -> extract -> merge -> persist."""

    def __init__(
        self,
        storage: Any,
        embed_fn: Callable[[str], Any] | None,
        embed_batch_fn: Callable[[list[str]], Any] | None,
        model: Any,
        config: dict[str, Any],
        extension_dir: Path | None = None,
        community_manager: Any | None = None,
    ) -> None:
        self._storage = storage
        self._embed_fn = embed_fn
        self._embed_batch_fn = embed_batch_fn
        self._model = model
        self._config = config
        self._extension_dir = extension_dir or Path(__file__).resolve().parent
        self._community_manager = community_manager
        self._entity_resolution_threshold = config.get("entity_resolution_threshold", 0.85)
        self._fact_dedup_threshold = config.get("fact_dedup_threshold", 0.90)
        self._preferred_predicates = config.get("preferred_predicates") or []
        self._preferred_predicates_set = set(self._preferred_predicates)
        self._preferred_predicate_embeddings: dict[str, list[float]] | None = None
        self._pipeline_max_attempts = config.get("pipeline_max_attempts", 3)

        self._resolver = EntityResolver(
            storage=storage,
            embed_fn=embed_fn,
            threshold=self._entity_resolution_threshold,
        )

        # Decompose agent
        decompose_instructions = self._render_prompt("decompose.jinja2", {})
        self._decompose_agent = Agent(
            name="MemoryDecomposeAgent",
            instructions=decompose_instructions,
            model=self._model,
            output_type=DecompositionResult,
            model_settings=ModelSettings(parallel_tool_calls=True),
        )

        # Extract agent
        extract_instructions = self._render_prompt(
            "extract.jinja2",
            {"atomic_fact": "", "preferred_predicates": self._preferred_predicates},
        )
        self._extract_agent = Agent(
            name="MemoryExtractAgent",
            instructions=extract_instructions,
            model=self._model,
            output_type=ExtractionResult,
            model_settings=ModelSettings(parallel_tool_calls=True),
        )

    def _render_prompt(self, template_name: str, vars: dict[str, Any]) -> str:
        """Render Jinja2 prompt from prompts/."""
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        prompts_dir = self._extension_dir / "prompts"
        env = Environment(
            loader=FileSystemLoader(str(prompts_dir)),
            autoescape=select_autoescape(enabled_extensions=()),
        )
        return env.get_template(template_name).render(**vars).strip()

    async def _ensure_preferred_predicate_embeddings(
        self, embed_fn: Callable[[str], Any]
    ) -> None:
        """Lazily compute embeddings for preferred predicates. Populates _preferred_predicate_embeddings."""
        if self._preferred_predicate_embeddings is not None:
            return
        if not self._preferred_predicates or not embed_fn:
            self._preferred_predicate_embeddings = {}
            return
        result: dict[str, list[float]] = {}
        for pp in self._preferred_predicates:
            try:
                text = pp.replace("_", " ").lower()
                emb = embed_fn(text)
                if asyncio.iscoroutine(emb):
                    emb = await emb
                if emb and isinstance(emb, list):
                    result[pp] = emb
            except Exception as e:
                logger.debug("Embed preferred predicate %r failed: %s", pp, e)
        self._preferred_predicate_embeddings = result

    async def _decompose_to_atomic_facts(self, episode: dict[str, Any]) -> list[str]:
        """Module 1: LLM decomposes episode into atomic facts. Returns list of fact strings."""
        prompt = self._render_prompt(
            "decompose.jinja2",
            {
                "actor": episode.get("actor", "user"),
                "content": episode.get("content", ""),
            },
        )
        try:
            result = await Runner.run(self._decompose_agent, prompt, max_turns=1)
            out = result.final_output
            if isinstance(out, DecompositionResult):
                return out.facts or []
            if isinstance(out, str):
                data = json.loads(out) if out.strip().startswith("{") else {"facts": []}
                return data.get("facts", [])
            return []
        except Exception as e:
            logger.warning("Decompose failed for episode %s: %s", episode.get("id", ""), e)
            return []

    async def _process_atomic_fact(
        self, atomic_text: str, episode: dict[str, Any]
    ) -> ExtractionResult | None:
        """Modules 2+3: LLM extracts structured triple from one atomic fact."""
        prompt = self._render_prompt(
            "extract.jinja2",
            {
                "atomic_fact": atomic_text,
                "preferred_predicates": self._preferred_predicates,
            },
        )
        try:
            result = await Runner.run(self._extract_agent, prompt, max_turns=1)
            out = result.final_output
            if isinstance(out, ExtractionResult):
                return out
            if isinstance(out, str):
                data = json.loads(out)
                return ExtractionResult(
                    subject=ExtractedEntity(**data.get("subject", {"name": "", "entity_type": ""})),
                    predicate=data.get("predicate", "RELATED_TO"),
                    object=ExtractedEntity(**data.get("object", {"name": "", "entity_type": ""})),
                    fact_text=data.get("fact_text", atomic_text),
                    t_valid=data.get("t_valid"),
                    t_invalid=data.get("t_invalid"),
                )
            return None
        except Exception as e:
            logger.warning("Extract failed for atomic fact %r: %s", atomic_text[:50], e)
            return None

    async def _merge_and_persist(
        self,
        results: list[ExtractionResult],
        episode_id: str,
        embed_fn: Callable[[str], Any] | None,
    ) -> int:
        """Module 4: Entity resolution, fact dedup, temporal conflict, predicate canonicalization."""
        if not results:
            return 0

        fact_count = 0
        entity_ids_seen: set[str] = set()

        # Resolve embeddings for entities
        subject_names = [r.subject.name for r in results]
        object_names = [r.object.name for r in results]
        all_names = list(dict.fromkeys(subject_names + object_names))

        embeddings_map: dict[str, list[float]] = {}
        if self._embed_batch_fn and all_names:
            try:
                embs = await self._embed_batch_fn(all_names)
                if embs and len(embs) == len(all_names):
                    embeddings_map = dict(zip(all_names, embs))
            except Exception as e:
                logger.debug("Batch embed failed: %s", e)

        for ext in results:
            sub_name = (ext.subject.name or "").strip()
            obj_name = (ext.object.name or "").strip()
            if not sub_name or not obj_name:
                continue

            sub_emb = embeddings_map.get(sub_name)
            obj_emb = embeddings_map.get(obj_name)

            subject_id, sub_new = await self._resolver.resolve(sub_name, sub_emb)
            object_id, obj_new = await self._resolver.resolve(obj_name, obj_emb)

            if sub_new:
                entity_ids_seen.add(subject_id)
            if obj_new:
                entity_ids_seen.add(object_id)

            # Save entity embeddings if new
            if sub_new and sub_emb:
                await self._storage.save_entity_embedding(subject_id, sub_emb)
            if obj_new and obj_emb:
                await self._storage.save_entity_embedding(object_id, obj_emb)

            # Link episode to entities
            await self._storage.link_episode_entity(episode_id, subject_id)
            await self._storage.link_episode_entity(episode_id, object_id)

            # Fact dedup: same (subject_id, object_id), similar fact_text
            existing = await self._storage.get_facts_for_entity_pair(subject_id, object_id)
            fact_text = (ext.fact_text or "").strip() or f"{sub_name} {ext.predicate} {obj_name}"

            fact_emb: list[float] | None = None
            if embed_fn:
                try:
                    raw = embed_fn(fact_text)
                    if asyncio.iscoroutine(raw):
                        raw = await raw
                    fact_emb = raw if isinstance(raw, list) else None
                except Exception:
                    pass

            skip_duplicate = False
            if fact_emb:
                try:
                    candidates = await self._storage.vec_search_facts(fact_emb, top_k=10)
                    for c in candidates:
                        if c["fact_id"] in {f["id"] for f in existing}:
                            dist = c.get("distance", float("inf"))
                            sim = max(0.0, 1.0 - (dist * dist) / 2.0)
                            if sim >= self._fact_dedup_threshold:
                                skip_duplicate = True
                                break
                except Exception:
                    pass
            elif not embed_fn:
                for f in existing:
                    if (f.get("fact_text") or "").strip() == fact_text:
                        skip_duplicate = True
                        break
            if skip_duplicate:
                continue

            # Predicate canonicalization
            predicate = (ext.predicate or "").upper() or "RELATED_TO"

            if (
                predicate not in self._preferred_predicates_set
                and embed_fn
                and self._preferred_predicates
            ):
                try:
                    await self._ensure_preferred_predicate_embeddings(embed_fn)
                    if self._preferred_predicate_embeddings:
                        pred_text = predicate.replace("_", " ").lower()
                        pred_emb = embed_fn(pred_text)
                        if asyncio.iscoroutine(pred_emb):
                            pred_emb = await pred_emb
                        if pred_emb and isinstance(pred_emb, list):
                            best_sim, best_pred = 0.0, predicate
                            for pp, pp_emb in self._preferred_predicate_embeddings.items():
                                sim = _cosine_similarity(pred_emb, pp_emb)
                                if sim > best_sim:
                                    best_sim, best_pred = sim, pp
                            if best_sim >= 0.90:
                                predicate = best_pred
                except Exception as e:
                    logger.debug("Predicate canonicalization failed: %s", e)

            fact_id = str(uuid.uuid4())
            fact = {
                "id": fact_id,
                "subject_id": subject_id,
                "predicate": predicate,
                "object_id": object_id,
                "fact_text": fact_text,
                "t_valid": ext.t_valid,
                "t_invalid": ext.t_invalid,
                "source_episode_id": episode_id,
                "confidence": 1.0,
            }
            await self._storage.insert_fact(fact)

            # Temporal conflict: expire old facts (same subject+predicate, different object)
            conflict_facts = await self._storage.get_facts_by_subject_predicate(
                subject_id, predicate
            )
            for cf in conflict_facts:
                if cf["id"] != fact_id and cf["object_id"] != object_id:
                    await self._storage.expire_fact(cf["id"], invalidated_by=fact_id)

            if fact_emb:
                try:
                    await self._storage.save_fact_embedding(fact_id, fact_emb)
                except Exception:
                    pass

            if self._community_manager:
                try:
                    if sub_new:
                        await self._community_manager.on_entity_added(subject_id)
                    if obj_new:
                        await self._community_manager.on_entity_added(object_id)
                except Exception as e:
                    logger.warning("community on_entity_added failed: %s", e)

            fact_count += 1

        return fact_count

    async def _merge_and_persist_single(
        self,
        ext: ExtractionResult,
        *,
        confidence: float = 1.0,
        source_episode_id: str | None = None,
    ) -> str | int:
        """Merge and persist a single extraction. Returns fact_id if created, 0 if dedup."""
        sub_name = (ext.subject.name or "").strip()
        obj_name = (ext.object.name or "").strip()
        if not sub_name or not obj_name:
            return 0

        embeddings_map: dict[str, list[float]] = {}
        if self._embed_batch_fn:
            try:
                embs = await self._embed_batch_fn([sub_name, obj_name])
                if embs and len(embs) >= 2:
                    embeddings_map = {sub_name: embs[0], obj_name: embs[1]}
            except Exception:
                pass
        sub_emb = embeddings_map.get(sub_name)
        obj_emb = embeddings_map.get(obj_name)

        subject_id, sub_new = await self._resolver.resolve(sub_name, sub_emb)
        object_id, obj_new = await self._resolver.resolve(obj_name, obj_emb)

        fact_text = (ext.fact_text or "").strip() or f"{sub_name} {ext.predicate} {obj_name}"
        existing = await self._storage.get_facts_for_entity_pair(subject_id, object_id)

        fact_emb: list[float] | None = None
        if self._embed_fn:
            try:
                raw = self._embed_fn(fact_text)
                if asyncio.iscoroutine(raw):
                    raw = await raw
                fact_emb = raw if isinstance(raw, list) else None
            except Exception:
                pass

        if fact_emb:
            try:
                candidates = await self._storage.vec_search_facts(fact_emb, top_k=10)
                for c in candidates:
                    if c["fact_id"] in {f["id"] for f in existing}:
                        dist = c.get("distance", float("inf"))
                        sim = max(0.0, 1.0 - (dist * dist) / 2.0)
                        if sim >= self._fact_dedup_threshold:
                            return 0
            except Exception:
                pass
        else:
            for f in existing:
                if (f.get("fact_text") or "").strip() == fact_text:
                    return 0

        predicate = (ext.predicate or "").upper() or "RELATED_TO"
        fact_id = str(uuid.uuid4())
        fact = {
            "id": fact_id,
            "subject_id": subject_id,
            "predicate": predicate,
            "object_id": object_id,
            "fact_text": fact_text,
            "t_valid": ext.t_valid,
            "t_invalid": ext.t_invalid,
            "source_episode_id": source_episode_id,
            "confidence": confidence,
        }
        await self._storage.insert_fact(fact)

        conflict_facts = await self._storage.get_facts_by_subject_predicate(
            subject_id, predicate
        )
        for cf in conflict_facts:
            if cf["id"] != fact_id and cf["object_id"] != object_id:
                await self._storage.expire_fact(cf["id"], invalidated_by=fact_id)

        if fact_emb:
            try:
                await self._storage.save_fact_embedding(fact_id, fact_emb)
            except Exception:
                pass

        if self._community_manager:
            try:
                if sub_new:
                    await self._community_manager.on_entity_added(subject_id)
                if obj_new:
                    await self._community_manager.on_entity_added(object_id)
            except Exception as e:
                logger.warning("community on_entity_added failed: %s", e)

        if source_episode_id:
            await self._storage.link_episode_entity(source_episode_id, subject_id)
            await self._storage.link_episode_entity(source_episode_id, object_id)

        return fact_id

    async def process_episode(self, episode: dict[str, Any]) -> int:
        """Process one episode through the pipeline. Returns fact count."""
        episode_id = episode.get("id", "")
        if not episode_id:
            return 0

        # Module 1: Decompose
        facts = await self._decompose_to_atomic_facts(episode)
        if not facts:
            return 0

        await self._storage.enqueue_atomic_facts(episode_id, facts)

        # Modules 2+3: Extract (parallel)
        extract_tasks = [self._process_atomic_fact(f, episode) for f in facts]
        raw_results = await asyncio.gather(*extract_tasks)
        results = [r for r in raw_results if r is not None]

        # Module 4: Merge and persist
        return await self._merge_and_persist(results, episode_id, self._embed_fn)

    async def process_session(self, session_id: str) -> PipelineResult:
        """Process all episodes for a session. Returns aggregate stats."""
        episodes = await self._storage.get_session_episodes(session_id, limit=100)
        result = PipelineResult(session_id=session_id)
        result.episodes_processed = len(episodes)

        for ep in episodes:
            try:
                n = await self.process_episode(ep)
                result.facts_created += n
            except Exception as e:
                logger.exception("process_episode failed: %s", e)
                result.errors.append(str(e))

        return result

    async def retry_failed(self) -> int:
        """Re-process failed/pending queue items. Returns count retried."""
        max_attempts = self._pipeline_max_attempts
        items = await self._storage.get_pending_queue_items(limit=100)
        retried = 0

        for item in items:
            if item.get("attempts", 0) >= max_attempts:
                continue

            item_id = item["id"]
            episode_id = item["episode_id"]
            atomic_fact = item.get("atomic_fact", "")

            episode = await self._storage.get_episode_by_id(episode_id)
            if not episode:
                await self._storage.update_queue_item_status(
                    item_id, "failed", "episode not found"
                )
                continue

            try:
                ext = await self._process_atomic_fact(atomic_fact, episode)
                if ext:
                    n = await self._merge_and_persist([ext], episode_id, self._embed_fn)
                    if n > 0:
                        retried += 1
                await self._storage.mark_queue_item_done(item_id)
            except Exception as e:
                await self._storage.update_queue_item_status(item_id, "failed", str(e))
                logger.warning("retry_failed item %s: %s", item_id[:8], e)

        return retried

    async def remember_fact_from_text(
        self, fact_text: str, confidence: float = 1.0
    ) -> tuple[str, str]:
        """Process a single user-provided fact (remember_fact tool). Returns (fact_id, status)."""
        import time as _time

        fact_text = (fact_text or "").strip()
        if not fact_text:
            return ("", "error: empty fact")

        synthetic_episode = {
            "id": "",
            "content": fact_text,
            "t_obs": int(_time.time() * 1000),
        }
        ext = await self._process_atomic_fact(fact_text, synthetic_episode)
        if not ext:
            return ("", "error: could not extract fact")

        sub_name = (ext.subject.name or "").strip()
        obj_name = (ext.object.name or "").strip()
        if not sub_name or not obj_name:
            return ("", "error: could not extract subject and object")

        result = await self._merge_and_persist_single(
            ext, confidence=confidence, source_episode_id=None
        )
        if result == 0:
            return ("", "already_exists")
        return (str(result), "saved")
