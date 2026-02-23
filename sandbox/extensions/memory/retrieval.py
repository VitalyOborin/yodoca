"""MemoryRetrieval: intent classification, FTS5 search, context assembly. Memory v2."""

import re
from abc import ABC, abstractmethod
from typing import Any


class IntentClassifier(ABC):
    """Strategy interface for intent classification."""

    @abstractmethod
    def classify(self, query: str) -> str:
        """Return intent: 'why' | 'when' | 'who' | 'what' | 'general'."""


class KeywordIntentClassifier(IntentClassifier):
    """Regex keyword matching. English-only, <1ms. Fallback classifier."""

    def classify(self, query: str) -> str:
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
    """FTS5 search + intent routing + context assembly. Phase 1: FTS5 only."""

    def __init__(
        self,
        storage: Any,
        intent_classifier: IntentClassifier,
    ) -> None:
        self._storage = storage
        self._intent_classifier = intent_classifier

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        token_budget: int = 2000,
        node_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search memory. Phase 1: FTS5 only. Default includes episodic (Phase 1 has no consolidation yet)."""
        if node_types is None:
            node_types = ["episodic", "semantic", "procedural", "opinion"]
        results = await self._storage.fts_search(
            query,
            node_types=node_types,
            limit=limit,
        )
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
