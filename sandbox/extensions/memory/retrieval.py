"""MemoryRetrieval: intent classification, FTS5 search, context assembly. Memory v2."""

import math
import re
from abc import ABC, abstractmethod
from typing import Any, Callable


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
            for kw in ("compare", "summarize", "everything", "all", "overview")
        ):
            conjunctions = len(re.findall(r"\b(and|or|but)\b", query.lower()))
            if conjunctions < 2:
                return "simple"
    return "complex"


def get_adaptive_params(complexity: str) -> dict[str, Any]:
    """Token budget and retrieval depth by complexity."""
    if complexity == "simple":
        return {"token_budget": 1000, "limit": 5}
    return {"token_budget": 3000, "limit": 20}


class MemoryRetrieval:
    """Hybrid FTS5 + vector search with RRF fusion. Intent-aware retrieval."""

    def __init__(
        self,
        storage: Any,
        intent_classifier: IntentClassifier,
        *,
        rrf_k: int = 60,
        rrf_weight_fts: float = 1.0,
        rrf_weight_vector: float = 1.0,
    ) -> None:
        self._storage = storage
        self._intent_classifier = intent_classifier
        self._k = rrf_k
        self._w_fts = rrf_weight_fts
        self._w_vec = rrf_weight_vector

    def _rrf_merge(
        self,
        fts_results: list[dict[str, Any]],
        vec_results: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Reciprocal Rank Fusion of FTS5 and vector results."""
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
    ) -> list[dict[str, Any]]:
        """Hybrid search: FTS5 + vector with RRF fusion. Default excludes episodic."""
        if node_types is None:
            node_types = ["semantic", "procedural", "opinion"]
        cand_limit = limit * 2
        fts_results = await self._storage.fts_search(
            query,
            node_types=node_types,
            limit=cand_limit,
        )
        if query_embedding is not None:
            vec_results = await self._storage.vector_search(
                query_embedding,
                node_types=node_types,
                limit=cand_limit,
            )
            results = self._rrf_merge(fts_results, vec_results, limit)
        else:
            results = fts_results[:limit]
        return results

    def assemble_context(
        self,
        results: list[dict[str, Any]],
        token_budget: int = 2000,
    ) -> str:
        """Format results as markdown. Phase 1: simplified 'Relevant memory' section."""
        if not results:
            return ""
        lines: list[str] = []
        approx_chars = 0
        char_per_token = 4
        max_chars = token_budget * char_per_token
        for r in results:
            content = r.get("content", "")
            if approx_chars + len(content) > max_chars:
                break
            lines.append(f"- {content}")
            approx_chars += len(content)
        return "## Relevant memory\n" + "\n".join(lines)
