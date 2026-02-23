"""MemoryRetrieval: intent classification, FTS5 search, context assembly. Memory v2."""

import logging
import math
import re
import time
from abc import ABC, abstractmethod
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

    def __init__(self, embed_fn: Callable[..., Any], threshold: float = 0.45) -> None:
        self._embed_fn = embed_fn
        self._threshold = threshold
        self._intent_embeddings: dict[str, list[list[float]]] = {}

    async def initialize(self) -> None:
        """Pre-embed exemplars at startup. One-time cost."""
        for intent, examples in self.EXEMPLARS.items():
            self._intent_embeddings[intent] = [
                await self._embed_fn(ex) for ex in examples
            ]

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
        return {"token_budget": 1000, "limit": 5, "graph_depth": 2}
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
    """Resolve entity by canonical_name or alias. Returns entity dict or None."""
    if not name or not name.strip():
        return None
    entity = await storage.get_entity_by_name(name.strip())
    if entity:
        return entity
    return await storage.search_entity_by_alias(name.strip())


class MemoryRetrieval:
    """Hybrid FTS5 + vector + graph search with RRF fusion. Intent-aware retrieval."""

    def __init__(
        self,
        storage: Any,
        intent_classifier: IntentClassifier,
        *,
        rrf_k: int = 60,
        rrf_weight_fts: float = 1.0,
        rrf_weight_vector: float = 1.0,
        rrf_weight_graph: float = 1.0,
    ) -> None:
        self._storage = storage
        self._intent_classifier = intent_classifier
        self._k = rrf_k
        self._w_fts = rrf_weight_fts
        self._w_vec = rrf_weight_vector
        self._w_graph = rrf_weight_graph

    def _rrf_merge(
        self,
        fts_results: list[dict[str, Any]],
        vec_results: list[dict[str, Any]],
        limit: int,
        graph_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Reciprocal Rank Fusion of FTS5, vector, and optional graph results."""
        scores: dict[str, float] = {}
        all_items: dict[str, dict[str, Any]] = {}
        for rank, item in enumerate(fts_results, 1):
            nid = item["id"]
            scores[nid] = scores.get(nid, 0) + self._w_fts / (self._k + rank)
            all_items.setdefault(nid, item)
        for rank, item in enumerate(vec_results, 1):
            nid = item["id"]
            scores[nid] = scores.get(nid, 0) + self._w_vec / (self._k + rank)
            all_items.setdefault(nid, item)
        if graph_results:
            for rank, item in enumerate(graph_results, 1):
                nid = item["id"]
                scores[nid] = scores.get(nid, 0) + self._w_graph / (self._k + rank)
                all_items.setdefault(nid, item)
        ranked = sorted(scores, key=scores.get, reverse=True)[:limit]
        return [all_items[nid] for nid in ranked]

    async def search(
        self,
        query: str,
        *,
        query_embedding: list[float] | None = None,
        limit: int = 10,
        token_budget: int = 2000,
        node_types: list[str] | None = None,
        entity_name: str | None = None,
        event_after: int | None = None,
        event_before: int | None = None,
        graph_depth: int | None = None,
    ) -> list[dict[str, Any]]:
        """Intent-aware hybrid search: FTS5 + vector + graph with RRF fusion."""
        if node_types is None:
            node_types = ["semantic", "procedural", "opinion"]
        cand_limit = max(limit * 2, 5)
        seed_limit = 5

        fts_results = await self._storage.fts_search(
            query,
            node_types=node_types,
            limit=cand_limit,
        )
        vec_results: list[dict[str, Any]] = []
        if query_embedding is not None:
            vec_results = await self._storage.vector_search(
                query_embedding,
                node_types=node_types,
                limit=cand_limit,
            )

        intent = self._intent_classifier.classify(
            query, query_embedding=query_embedding
        )
        depth = graph_depth if graph_depth is not None else 3
        graph_results: list[dict[str, Any]] = []

        if intent in ("why", "when", "who", "what"):
            seed_ids = [r["id"] for r in (fts_results[:seed_limit] or vec_results[:seed_limit])]

            if intent == "why" and seed_ids:
                graph_results = await self._storage.causal_chain_traversal(
                    seed_ids[0], max_depth=depth, limit=limit
                )

            elif intent == "when" and seed_ids:
                forward = await self._storage.temporal_chain_traversal(
                    seed_ids,
                    direction="forward",
                    max_depth=depth,
                    limit=limit,
                    event_after=event_after,
                    event_before=event_before,
                )
                backward = await self._storage.temporal_chain_traversal(
                    seed_ids,
                    direction="backward",
                    max_depth=depth,
                    limit=limit,
                    event_after=event_after,
                    event_before=event_before,
                )
                seen: set[str] = set()
                for r in forward + backward:
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        graph_results.append(r)

            elif intent in ("who", "what"):
                entity = await _resolve_entity(
                    self._storage,
                    entity_name or query,
                )
                if entity:
                    graph_results = await self._storage.entity_nodes_for_entity(
                        entity["id"],
                        node_types=node_types,
                        limit=limit,
                    )

        results = self._rrf_merge(
            fts_results,
            vec_results,
            limit,
            graph_results=graph_results if graph_results else None,
        )
        if results:
            node_ids = [r["id"] for r in results]
            await self._storage.record_access_for_nodes(node_ids)
        logger.debug(
            "search: query=%r intent=%s fts=%d vec=%d graph=%d merged=%d",
            query[:60], intent, len(fts_results), len(vec_results),
            len(graph_results), len(results),
        )
        return results

    async def assemble_context(
        self,
        results: list[dict[str, Any]],
        token_budget: int = 2000,
    ) -> str:
        """Format results with budget shares: Facts 40%, Entity profiles 25%, Temporal 25%, Evidence 10%."""
        if not results:
            return ""
        char_per_token = 4
        total_chars = token_budget * char_per_token
        budget_facts = int(total_chars * 0.40)
        budget_entities = int(total_chars * 0.25)
        budget_temporal = int(total_chars * 0.25)
        budget_evidence = int(total_chars * 0.10)

        facts = [r for r in results if r.get("type") in ("semantic", "procedural", "opinion")]
        episodic = [r for r in results if r.get("type") == "episodic"]
        node_ids = [r["id"] for r in results]

        sections: list[str] = []

        if facts:
            lines = []
            chars = 0
            for r in facts:
                c = r.get("content", "")
                if chars + len(c) + 4 > budget_facts:
                    break
                lines.append(f"- {c}")
                chars += len(c) + 4
            if lines:
                sections.append("## Facts\n" + "\n".join(lines))

        entities = await self._storage.get_entities_for_nodes(node_ids)
        if entities:
            lines = []
            chars = 0
            for e in entities[:5]:
                name = e.get("canonical_name", "")
                summary = e.get("summary") or "(no summary)"
                block = f"**{name}**: {summary}"
                if chars + len(block) + 2 > budget_entities:
                    break
                lines.append(block)
                chars += len(block) + 2
            if lines:
                sections.append("## Entity profiles\n" + "\n".join(lines))

        if episodic:
            lines = []
            chars = 0
            for r in sorted(episodic, key=lambda x: x.get("event_time", 0)):
                c = r.get("content", "")
                if chars + len(c) + 4 > budget_temporal:
                    break
                lines.append(f"- {c}")
                chars += len(c) + 4
            if lines:
                sections.append("## Temporal context\n" + "\n".join(lines))
        else:
            episode_ids: list[str] = []
            for r in facts[:3]:
                targets = await self._storage.get_derived_from_targets(r["id"])
                episode_ids.extend(targets)
            if episode_ids:
                episodes = await self._storage.get_nodes_by_ids(episode_ids)
                lines = []
                chars = 0
                for ep in sorted(episodes, key=lambda x: x.get("event_time", 0)):
                    c = ep.get("content", "")
                    if chars + len(c) + 4 > budget_temporal:
                        break
                    lines.append(f"- {c}")
                    chars += len(c) + 4
                if lines:
                    sections.append("## Temporal context\n" + "\n".join(lines))

        if facts and budget_evidence > 0:
            evidence_lines = []
            chars = 0
            for r in facts[:2]:
                targets = await self._storage.get_derived_from_targets(r["id"])
                if targets:
                    nodes = await self._storage.get_nodes_by_ids(targets[:1])
                    if nodes:
                        src = nodes[0].get("content", "")[:200]
                        block = f"- Source: {src}..."
                        if chars + len(block) + 2 <= budget_evidence:
                            evidence_lines.append(block)
                            chars += len(block) + 2
            if evidence_lines:
                sections.append("## Evidence\n" + "\n".join(evidence_lines))

        if not sections:
            lines = []
            chars = 0
            max_chars = total_chars
            for r in results:
                c = r.get("content", "")
                if chars + len(c) + 4 > max_chars:
                    break
                lines.append(f"- {c}")
                chars += len(c) + 4
            return "## Relevant memory\n" + "\n".join(lines) if lines else ""

        return "\n\n".join(sections)
