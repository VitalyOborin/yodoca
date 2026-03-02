"""HierarchicalRetriever: intent classification, hybrid fact search, BFS expansion, RRF fusion. Memory v3."""

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity: dot(a,b) / (norm(a) * norm(b))."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class IntentClassifier(ABC):
    """Strategy interface for intent classification."""

    @abstractmethod
    def classify(self, query: str, **kwargs: Any) -> str:
        """Return intent: 'why' | 'when' | 'who' | 'what' | 'general'."""


class KeywordIntentClassifier(IntentClassifier):
    """Regex keyword matching. English-only, <1ms. Fallback classifier."""

    def classify(self, query: str, **kwargs: Any) -> str:
        q = query.strip().lower()
        if re.search(
            r"\b(why|cause|caused|reason|because|led to|resulted in)\b",
            q,
        ):
            return "why"
        if re.search(
            r"\b(when|after|before|during|timeline|sequence|then|next|previous)\b",
            q,
        ):
            return "when"
        if re.search(r"\b(who|whom|whose)\b", q):
            return "who"
        if re.search(
            r"\b(what|which|everything about|tell me about)\b",
            q,
        ):
            return "what"
        return "general"


class EmbeddingIntentClassifier(IntentClassifier):
    """Cosine similarity against intent exemplars. Multilingual, <2ms."""

    EXEMPLARS: dict[str, list[str]] = {
        "why": [
            "why did this happen",
            "what caused the failure",
            "what is the reason",
            "explain the cause",
            "почему это произошло",
            "в чем причина",
            "из-за чего",
        ],
        "when": [
            "when did we discuss",
            "what happened after",
            "timeline of events",
            "before the meeting",
            "когда мы обсуждали",
            "после встречи",
            "хронология событий",
        ],
        "who": [
            "who is responsible",
            "who said that",
            "whose idea",
            "кто отвечает за",
            "чья это идея",
            "кто сказал",
        ],
        "what": [
            "what do you know about",
            "tell me everything about",
            "what is the status",
            "which project",
            "что ты знаешь о",
            "расскажи всё о",
            "какой статус",
        ],
    }

    def __init__(
        self,
        embed_fn: Callable[..., Any],
        threshold: float = 0.45,
        *,
        embed_batch_fn: Callable[..., Any] | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._embed_fn = embed_fn
        self._embed_batch_fn = embed_batch_fn
        self._cache_dir = cache_dir
        self._threshold = threshold
        self._intent_embeddings: dict[str, list[list[float]]] = {}

    def _resolve_cache_path(self) -> Path | None:
        """Compute cache file path from exemplar text hash. Returns None if cache_dir not set."""
        if not self._cache_dir:
            return None
        canonical = json.dumps(
            {k: sorted(v) for k, v in sorted(self.EXEMPLARS.items())},
            sort_keys=True,
            ensure_ascii=False,
        )
        h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
        return self._cache_dir / f"intent_embeddings_{h}.json"

    def _load_cache(self, path: Path) -> dict[str, list[list[float]]]:
        """Load intent embeddings from JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, list)}

    def _save_cache(self, path: Path) -> None:
        """Save intent embeddings to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._intent_embeddings, ensure_ascii=False),
            encoding="utf-8",
        )

    async def initialize(self) -> None:
        """Pre-embed exemplars at startup. Cache-first, then batch or sequential."""
        cache_path = self._resolve_cache_path()
        if cache_path and cache_path.exists():
            self._intent_embeddings = self._load_cache(cache_path)
            logger.info("Intent embeddings loaded from cache")
            return

        all_texts: list[str] = []
        intent_ranges: list[tuple[str, int, int]] = []
        for intent, examples in self.EXEMPLARS.items():
            start = len(all_texts)
            all_texts.extend(examples)
            intent_ranges.append((intent, start, len(all_texts)))

        if self._embed_batch_fn:
            embeddings = await self._embed_batch_fn(all_texts)
        else:
            embeddings = [await self._embed_fn(t) for t in all_texts]

        for intent, start, end in intent_ranges:
            self._intent_embeddings[intent] = [
                e for e in embeddings[start:end] if e is not None
            ]

        if cache_path:
            self._save_cache(cache_path)
            logger.info("Intent embeddings cached to %s", cache_path.name)

    def classify(self, query: str, **kwargs: Any) -> str:
        """Classify intent. Accepts pre-computed query_embedding via kwargs."""
        query_embedding = kwargs.get("query_embedding")
        if query_embedding is None:
            return "general"
        best_intent, best_score = "general", 0.0
        for intent, embs in self._intent_embeddings.items():
            score = max(cosine_sim(query_embedding, e) for e in embs)
            if score > best_score:
                best_intent, best_score = intent, score
        return best_intent if best_score > self._threshold else "general"


def classify_query_complexity(query: str) -> str:
    """Heuristic: 'simple' | 'complex'. From ADR section 11.5."""
    words = query.split()
    if len(words) < 10:
        if not any(
            kw in query.lower()
            for kw in (
                "compare",
                "summarize",
                "everything",
                "all",
                "overview",
                "всё",
                "все",
                "расскажи",
                "подробно",
                "обзор",
                "сравни",
            )
        ):
            conjunctions = len(re.findall(r"\b(and|or|but)\b", query.lower()))
            if conjunctions < 2:
                return "simple"
    return "complex"


def get_adaptive_params(complexity: str) -> dict[str, Any]:
    """Token budget, retrieval depth, and graph depth by complexity."""
    if complexity == "simple":
        return {"token_budget": 600, "limit": 3, "graph_depth": 2}
    return {"token_budget": 3000, "limit": 20, "graph_depth": 4}


def parse_time_expression(expr: str | None) -> int | None:
    """Parse time expression to Unix timestamp. last_week, last_month, YYYY-MM-DD."""
    if not expr or not expr.strip():
        return None
    expr = expr.strip().lower()
    now = int(time.time())
    if expr == "last_week":
        return now - 7 * 86400
    if expr == "last_month":
        return now - 30 * 86400
    if re.match(r"^\d{4}-\d{2}-\d{2}$", expr):
        try:
            from datetime import datetime

            dt = datetime.strptime(expr, "%Y-%m-%d")
            return int(dt.timestamp())
        except ValueError:
            return None
    return None


async def _resolve_entity(storage: Any, name: str) -> dict[str, Any] | None:
    """Resolve entity by name or alias. Returns entity dict or None."""
    if not name or not name.strip():
        return None
    entity = await storage.get_entity_by_normalized_name(name.strip())
    if entity:
        return entity
    return await storage.get_entity_by_alias(name.strip())


class HierarchicalRetriever:
    """Three-tier hierarchical retrieval: fact search (FTS5 + vector), BFS expansion, RRF fusion."""

    def __init__(
        self,
        storage: Any,
        embed_fn: Callable[..., Any],
        intent_classifier: IntentClassifier,
        *,
        rrf_k: int = 60,
        rrf_weight_fts: float = 1.0,
        rrf_weight_vector: float = 1.0,
        rrf_weight_graph: float = 1.0,
        bfs_max_depth: int = 2,
        bfs_max_facts: int = 50,
        context_token_budget: int = 2000,
    ) -> None:
        self._storage = storage
        self._embed_fn = embed_fn
        self._intent_classifier = intent_classifier
        self._k = rrf_k
        self._w_fts = rrf_weight_fts
        self._w_vec = rrf_weight_vector
        self._w_graph = rrf_weight_graph
        self._bfs_max_depth = bfs_max_depth
        self._bfs_max_facts = bfs_max_facts
        self._context_token_budget = context_token_budget

    def _rrf_merge(
        self,
        fts_results: list[dict[str, Any]],
        vec_results: list[dict[str, Any]],
        limit: int,
        graph_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Reciprocal Rank Fusion of FTS5, vector, and optional BFS graph results."""
        scores: dict[str, float] = {}
        all_items: dict[str, dict[str, Any]] = {}
        for rank, item in enumerate(fts_results, 1):
            fid = item["id"]
            scores[fid] = scores.get(fid, 0) + self._w_fts / (self._k + rank)
            all_items.setdefault(fid, item)
        for rank, item in enumerate(vec_results, 1):
            fid = item["id"]
            scores[fid] = scores.get(fid, 0) + self._w_vec / (self._k + rank)
            all_items.setdefault(fid, item)
        if graph_results:
            for rank, item in enumerate(graph_results, 1):
                fid = item["id"]
                scores[fid] = scores.get(fid, 0) + self._w_graph / (self._k + rank)
                all_items.setdefault(fid, item)
        ranked = sorted(scores, key=scores.get, reverse=True)[:limit]
        return [{**all_items[fid], "_rrf_score": scores[fid]} for fid in ranked]

    async def _search_facts(
        self,
        query: str,
        embedding: list[float] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Hybrid FTS5 + vector fact search. Merges with RRF."""
        fts_task = self._storage.fts_search_facts(query, limit=limit)
        vec_facts: list[dict[str, Any]] = []
        if embedding:
            vec_raw = await self._storage.vec_search_facts(embedding, top_k=limit)
            if vec_raw:
                fact_ids = [r["fact_id"] for r in vec_raw]
                vec_facts = await self._storage.get_facts_by_ids(fact_ids)
                # Preserve rank order from vec search
                by_id = {f["id"]: f for f in vec_facts}
                vec_facts = [by_id[fid] for fid in fact_ids if fid in by_id]
        fts_results = await fts_task
        return self._rrf_merge(fts_results, vec_facts, limit, graph_results=None)

    async def _search_entities(
        self,
        embedding: list[float] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Vector search on entities. Returns entity dicts for BFS expansion."""
        if not embedding:
            return []
        raw = await self._storage.vec_search_entities(embedding, top_k=limit)
        entity_ids = [r["entity_id"] for r in raw]
        if not entity_ids:
            return []
        # Fetch full entity dicts for those we need for BFS (BFS only needs IDs)
        return [{"id": eid} for eid in entity_ids]

    async def _bfs_expand(
        self,
        entity_ids: list[str],
        depth: int | None = None,
        max_facts: int | None = None,
    ) -> list[dict[str, Any]]:
        """BFS expansion from entities along fact-edges."""
        if not entity_ids:
            return []
        depth = depth if depth is not None else self._bfs_max_depth
        max_facts = max_facts if max_facts is not None else self._bfs_max_facts
        return await self._storage.bfs_expand_facts(
            entity_ids,
            max_depth=depth,
            max_facts=max_facts,
        )

    async def _search_communities(
        self, embedding: list[float] | None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Vector search on vec_communities. Returns community dicts."""
        if not embedding:
            return []
        raw = await self._storage.vec_search_communities(embedding, top_k=limit)
        if not raw:
            return []
        result = []
        for r in raw:
            c = await self._storage.get_community_by_id(r["community_id"])
            if c:
                result.append(c)
        return result

    async def search(
        self,
        query: str,
        *,
        query_embedding: list[float] | None = None,
        limit: int = 10,
        token_budget: int = 2000,
        entity_name: str | None = None,
        event_after: int | None = None,
        event_before: int | None = None,
    ) -> list[dict[str, Any]]:
        """Intent-aware hierarchical search. Returns ranked fact dicts."""
        complexity = classify_query_complexity(query)
        params = get_adaptive_params(complexity)
        limit = min(limit, params["limit"])
        graph_depth = params.get("graph_depth", self._bfs_max_depth)

        embedding = query_embedding
        if embedding is None and self._embed_fn:
            try:
                emb = self._embed_fn(query)
                if asyncio.iscoroutine(emb):
                    emb = await emb
                embedding = emb if isinstance(emb, list) else getattr(emb, "embedding", None)
            except Exception as e:
                logger.debug("embed failed for search: %s", e)

        intent = self._intent_classifier.classify(
            query, query_embedding=embedding
        )

        # Boost BFS depth for who/what
        if intent in ("who", "what"):
            graph_depth = min(graph_depth + 1, 4)

        # Parallel: fact search + entity search
        fact_limit = limit * 2
        facts_task = self._search_facts(query, embedding, limit=fact_limit)
        entities_task = self._search_entities(embedding, limit=10)
        facts, entity_results = await asyncio.gather(facts_task, entities_task)

        entity_ids = [e["id"] for e in entity_results]

        # Optional: include entity from entity_name for BFS
        if entity_name:
            resolved = await _resolve_entity(self._storage, entity_name)
            if resolved:
                entity_ids.insert(0, resolved["id"])

        bfs_facts = await self._bfs_expand(
            list(dict.fromkeys(entity_ids)),
            depth=graph_depth,
        )

        # RRF fusion: facts + BFS
        merged = self._rrf_merge(
            facts,
            bfs_facts,
            limit,
            graph_results=bfs_facts if bfs_facts else None,
        )

        # Filter by time if when intent
        if intent == "when" and (event_after is not None or event_before is not None):
            def in_range(f: dict[str, Any]) -> bool:
                tv = f.get("t_valid") or f.get("t_created")
                if tv is None:
                    return True
                if event_after is not None and tv < event_after:
                    return False
                if event_before is not None and tv > event_before:
                    return False
                return True

            merged = [f for f in merged if in_range(f)]

        result = merged[:limit]
        if result and self._storage:
            fact_ids = [f["id"] for f in result if f.get("id")]
            if fact_ids:
                self._storage.record_fact_access(fact_ids)
        return result

    async def assemble_context(
        self,
        results: list[dict[str, Any]],
        token_budget: int = 2000,
        community_summaries: list[dict[str, Any]] | None = None,
    ) -> str:
        """Three-section assembly: 50% facts, 25% entity profiles, 25% community."""
        budget = token_budget or self._context_token_budget
        fact_budget = int(budget * 0.5)
        entity_budget = int(budget * 0.25)
        community_budget = int(budget * 0.25)

        parts: list[str] = []

        # Section 1: Facts
        fact_ids = [f["id"] for f in results]
        entities_map = await self._storage.get_entities_for_facts(fact_ids)
        lines: list[str] = []
        approx = 0
        for f in results:
            if approx >= fact_budget:
                break
            subj = entities_map.get(f["subject_id"], {}).get("name", f["subject_id"])
            obj = entities_map.get(f["object_id"], {}).get("name", f["object_id"])
            pred = f.get("predicate", "RELATED")
            text = f.get("fact_text", "")
            conf = f.get("confidence", 1.0)
            tv = f.get("t_valid") or f.get("t_created")
            line = f"[{subj}] --[{pred}]--> [{obj}]: {text} (confidence={conf}, t_valid={tv})"
            lines.append(line)
            approx += len(line.split()) * 2
        if lines:
            parts.append("## Facts\n" + "\n".join(lines))

        # Section 2: Entity profiles (25%)
        entity_ids = list(
            dict.fromkeys(
                eid
                for f in results
                for eid in (f.get("subject_id"), f.get("object_id"))
                if eid and eid in entities_map
            )
        )[:5]
        if entity_ids and entity_budget > 0:
            profile_lines: list[str] = []
            for eid in entity_ids:
                ent = entities_map.get(eid)
                if not ent:
                    continue
                name = ent.get("name", eid)
                summary = ent.get("summary") or "(no summary)"
                profile_lines.append(f"- **{name}**: {summary}")
            if profile_lines:
                parts.append("## Entity profiles\n" + "\n".join(profile_lines))

        # Section 3: Community context (25%)
        if community_budget > 0 and community_summaries:
            comm_lines = [
                f"- **{c.get('name', '')}**: {c.get('summary', '')}"
                for c in community_summaries[:3]
            ]
            if comm_lines:
                parts.append("## Community context\n" + "\n".join(comm_lines))

        return "\n\n".join(parts) if parts else ""

    async def get_timeline(
        self,
        entity_id: str | None = None,
        *,
        after: int | None = None,
        before: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Direct temporal query on fact graph. No RRF fusion."""
        return await self._storage.get_entity_facts_timeline(
            entity_id,
            after=after,
            before=before,
            limit=limit,
        )
