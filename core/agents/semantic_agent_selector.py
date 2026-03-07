"""Semantic agent selection with local SQLite-backed vector retrieval.

Uses embedding capability when available, falls back to lexical ranking.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiosqlite
from pydantic import BaseModel, Field

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "you",
    "agent",
    "task",
    "как",
    "что",
    "это",
    "для",
    "или",
}


class AgentProfile(BaseModel):
    """Metadata used for semantic/lexical agent selection."""

    agent_id: str
    name: str
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    sample_queries: list[str] = Field(default_factory=list)
    integration_mode: str = "tool"


class RankedAgent(BaseModel):
    """Scored agent candidate."""

    agent_id: str
    score: float


class AgentSelectionResult(BaseModel):
    """Selection output for orchestration tools."""

    strategy: str
    selected_agent_ids: list[str] = Field(default_factory=list)
    candidates: list[RankedAgent] = Field(default_factory=list)


class SemanticAgentSelector:
    """Select top-k agents with vector similarity and lexical fallback."""

    def __init__(
        self,
        catalog_getter: Callable[[], list[AgentProfile]],
        db_path: Path,
        embed_batch: Callable[[list[str]], Awaitable[list[list[float] | None]]]
        | None = None,
    ) -> None:
        self._catalog_getter = catalog_getter
        self._db_path = db_path
        self._embed_batch = embed_batch
        self._lock = asyncio.Lock()
        self._last_signature: str | None = None

    async def select_agents(self, task: str, top_k: int = 3) -> AgentSelectionResult:
        profiles = self._catalog_getter()
        if not profiles:
            return AgentSelectionResult(strategy="none", selected_agent_ids=[])

        signature = self._signature(profiles)
        await self._ensure_index(profiles, signature)

        if self._embed_batch is not None:
            semantic = await self._semantic_select(task=task, top_k=top_k)
            if semantic.selected_agent_ids:
                return semantic

        return self._lexical_select(task=task, profiles=profiles, top_k=top_k)

    def _signature(self, profiles: list[AgentProfile]) -> str:
        payload = [
            {
                "agent_id": p.agent_id,
                "name": p.name,
                "description": p.description,
                "tools": sorted(p.tools),
                "sample_queries": sorted(p.sample_queries),
                "integration_mode": p.integration_mode,
            }
            for p in sorted(profiles, key=lambda x: x.agent_id)
        ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _ensure_index(self, profiles: list[AgentProfile], signature: str) -> None:
        if signature == self._last_signature:
            return
        async with self._lock:
            if signature == self._last_signature:
                return
            if self._embed_batch is None:
                self._last_signature = signature
                return
            vectors: list[tuple[str, list[float]]] = []
            for profile in profiles:
                utterances = self._profile_utterances(profile)
                raw_vectors = await self._embed_batch(utterances)
                pooled = self._mean_vector([v for v in raw_vectors if v is not None])
                if pooled is None:
                    continue
                vectors.append((profile.agent_id, pooled))
            await self._save_vectors(vectors)
            self._last_signature = signature

    async def _save_vectors(self, vectors: list[tuple[str, list[float]]]) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_selector_vector (
                    agent_id TEXT PRIMARY KEY,
                    vector_json TEXT NOT NULL
                )
                """
            )
            await db.execute("DELETE FROM agent_selector_vector")
            for agent_id, vector in vectors:
                await db.execute(
                    "INSERT INTO agent_selector_vector(agent_id, vector_json) VALUES(?, ?)",
                    (agent_id, json.dumps(vector, ensure_ascii=False)),
                )
            await db.commit()

    async def _load_vectors(self) -> dict[str, list[float]]:
        if not self._db_path.exists():
            return {}
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT agent_id, vector_json FROM agent_selector_vector"
            )
        result: dict[str, list[float]] = {}
        for agent_id, vec_json in rows:
            try:
                vec = json.loads(vec_json)
                if isinstance(vec, list):
                    result[str(agent_id)] = [float(v) for v in vec]
            except Exception:
                continue
        return result

    async def _semantic_select(self, task: str, top_k: int) -> AgentSelectionResult:
        if self._embed_batch is None:
            return AgentSelectionResult(strategy="semantic", selected_agent_ids=[])
        q_vecs = await self._embed_batch([task])
        q_vec = q_vecs[0] if q_vecs else None
        if q_vec is None:
            return AgentSelectionResult(strategy="semantic", selected_agent_ids=[])
        vectors = await self._load_vectors()
        if not vectors:
            return AgentSelectionResult(strategy="semantic", selected_agent_ids=[])

        ranked = [
            RankedAgent(agent_id=aid, score=self._cosine(q_vec, vec))
            for aid, vec in vectors.items()
        ]
        ranked.sort(key=lambda r: (-r.score, r.agent_id))
        ranked = ranked[: max(0, top_k)]
        selected = [r.agent_id for r in ranked if r.score > 0]
        return AgentSelectionResult(
            strategy="semantic",
            selected_agent_ids=selected,
            candidates=ranked,
        )

    def _lexical_select(
        self, task: str, profiles: list[AgentProfile], top_k: int
    ) -> AgentSelectionResult:
        terms = self._tokenize(task)
        ranked: list[RankedAgent] = []
        for profile in profiles:
            pool = self._tokenize(self._profile_text(profile))
            overlap = len(terms.intersection(pool))
            score = float(overlap)
            ranked.append(RankedAgent(agent_id=profile.agent_id, score=score))
        ranked.sort(key=lambda r: (-r.score, r.agent_id))
        ranked = ranked[: max(0, top_k)]
        selected = [r.agent_id for r in ranked if r.score > 0]
        if not selected and ranked:
            selected = [ranked[0].agent_id]
        return AgentSelectionResult(
            strategy="lexical",
            selected_agent_ids=selected,
            candidates=ranked,
        )

    def _profile_text(self, profile: AgentProfile) -> str:
        return "\n".join(
            [
                profile.name,
                profile.description,
                " ".join(profile.tools),
                " ".join(profile.sample_queries),
            ]
        )

    def _profile_utterances(self, profile: AgentProfile) -> list[str]:
        utterances = [profile.name, profile.description]
        utterances.extend(profile.sample_queries)
        if profile.tools:
            utterances.append("tools: " + " ".join(profile.tools))
        return [u for u in utterances if u and u.strip()]

    def _mean_vector(self, vectors: list[list[float]]) -> list[float] | None:
        if not vectors:
            return None
        dim = len(vectors[0])
        if dim == 0:
            return None
        for vec in vectors:
            if len(vec) != dim:
                return None
        return [sum(vec[i] for vec in vectors) / len(vectors) for i in range(dim)]

    def _tokenize(self, text: str) -> set[str]:
        return {
            t.lower()
            for t in _TOKEN_RE.findall(text or "")
            if len(t) >= 3 and t.lower() not in _STOPWORDS
        }

    def _cosine(self, a: list[float], b: list[float]) -> float:
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


__all__ = [
    "AgentProfile",
    "AgentSelectionResult",
    "RankedAgent",
    "SemanticAgentSelector",
]
