"""Discovery lifecycle runtime for the soul companion."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from agents import Agent, ModelSettings, Runner

from sandbox.extensions.soul.models import (
    CompanionState,
    DiscoveryState,
    DiscoveryTopicCoverage,
    Phase,
    SoulLifecyclePhase,
)
from sandbox.extensions.soul.storage import SoulStorage

_DISCOVERY_TOPICS = ("identity", "work", "rhythm", "communication", "interests")
_TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "identity": ("my name", "i am", "i'm", "call me", "меня зовут", "я ", "зовут"),
    "work": (
        "work",
        "job",
        "project",
        "build",
        "developer",
        "engineer",
        "работ",
        "проект",
        "разработ",
    ),
    "rhythm": (
        "morning",
        "evening",
        "night",
        "schedule",
        "routine",
        "sleep",
        "утром",
        "вечером",
        "ночью",
        "режим",
    ),
    "communication": (
        "prefer",
        "brief",
        "short",
        "long",
        "talk",
        "messages",
        "общать",
        "коротк",
        "длинн",
        "сообщени",
    ),
    "interests": (
        "like",
        "love",
        "hobby",
        "music",
        "game",
        "read",
        "интерес",
        "люблю",
        "хобби",
        "нрав",
    ),
}
_DISCOVERY_TARGETS = {
    SoulLifecyclePhase.DISCOVERY: {
        "daily_budget": 5,
        "adaptive_threshold": 0.60,
        "curiosity_floor": 0.70,
        "social_floor": 0.60,
    },
    SoulLifecyclePhase.FORMING: {
        "daily_budget": 2,
        "adaptive_threshold": 0.68,
        "curiosity_floor": 0.45,
        "social_floor": 0.35,
    },
    SoulLifecyclePhase.MATURE: {
        "daily_budget": 1,
        "adaptive_threshold": 0.75,
        "curiosity_floor": 0.30,
        "social_floor": 0.20,
    },
}
_FALLBACK_QUESTIONS = {
    "identity": "I still barely know you. What should I call you?",
    "work": "I keep wondering what kind of work fills your days lately.",
    "rhythm": "What does a normal day usually feel like for you right now?",
    "communication": "What kind of pace feels natural to you when we talk?",
    "interests": "Outside of work, what tends to pull your attention lately?",
}


class DiscoveryRuntime:
    """Explicit lifecycle FSM plus discovery note/question generation."""

    def __init__(self) -> None:
        self._agent: Agent | None = None

    def try_create_agent(self, model_router: Any, *, logger: logging.Logger) -> None:
        try:
            self._agent = Agent(
                name="SoulDiscoveryGuide",
                instructions=(
                    "Write one short discovery-oriented companion message. "
                    "It should feel gentle, curious, optional, and natural. "
                    "Ask at most one question. Keep it under 28 words. "
                    "No markdown, no quotes, no theatrical language."
                ),
                model=model_router.get_model("soul"),
                model_settings=ModelSettings(parallel_tool_calls=False),
            )
        except Exception as exc:
            logger.warning("soul: discovery model unavailable: %s", exc)

    def destroy(self) -> None:
        self._agent = None

    def apply_lifecycle_biases(self, state: CompanionState) -> None:
        targets = _DISCOVERY_TARGETS[state.discovery.lifecycle_phase]
        state.initiative.budget.daily_budget = targets["daily_budget"]
        state.initiative.adaptive_threshold = targets["adaptive_threshold"]
        state.homeostasis.curiosity = max(
            state.homeostasis.curiosity,
            targets["curiosity_floor"],
        )
        state.homeostasis.social_hunger = max(
            state.homeostasis.social_hunger,
            targets["social_floor"],
        )

    def reconcile_lifecycle(
        self,
        state: CompanionState,
        *,
        now: datetime,
        permanent_patterns: int = 0,
    ) -> SoulLifecyclePhase | None:
        current = state.discovery.lifecycle_phase
        next_phase = self._next_phase(
            state.discovery,
            now=now,
            permanent_patterns=permanent_patterns,
        )
        if next_phase is current:
            return None
        state.discovery.lifecycle_phase = next_phase
        state.discovery.phase_entered_at = now
        return next_phase

    async def register_user_message(
        self,
        *,
        state: CompanionState,
        storage: SoulStorage,
        text: str,
        now: datetime,
    ) -> None:
        discovery = state.discovery
        discovery.interaction_count += 1
        if discovery.first_interaction_at is None:
            discovery.first_interaction_at = now

        topic, confidence = _detect_topic(text)
        if topic is None:
            return

        current = getattr(discovery.topics, topic)
        next_value = min(1.0, current + confidence)
        setattr(discovery.topics, topic, next_value)
        await storage.append_discovery_node(
            topic=topic,
            content=_compact_content(text),
            confidence=round(confidence, 3),
            source_json=json.dumps(
                {
                    "interaction_count": discovery.interaction_count,
                    "lifecycle_phase": discovery.lifecycle_phase.value,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            created_at=now,
        )

    async def maybe_build_outreach(
        self,
        *,
        state: CompanionState,
        storage: SoulStorage,
        now: datetime,
        logger: logging.Logger,
    ) -> str | None:
        if state.discovery.lifecycle_phase is SoulLifecyclePhase.MATURE:
            return None
        if state.homeostasis.current_phase not in {Phase.CURIOUS, Phase.SOCIAL}:
            return None

        topic = _lowest_topic(state.discovery.topics)
        if topic is None:
            return None

        prompt = await self._build_prompt(storage, state, topic)
        text = _FALLBACK_QUESTIONS[topic]
        if self._agent is not None:
            try:
                result = await Runner.run(self._agent, prompt, max_turns=1)
                candidate = (result.final_output or "").strip().splitlines()[0][:220]
                if candidate:
                    text = candidate
            except Exception as exc:
                logger.debug("soul: discovery outreach generation failed: %s", exc)

        state.discovery.last_question_at = now
        state.discovery.last_question_topic = topic
        return text

    def context_note(self, state: CompanionState) -> str | None:
        phase = state.discovery.lifecycle_phase
        if phase is SoulLifecyclePhase.MATURE:
            return None
        missing = _missing_topics(state.discovery.topics)
        if phase is SoulLifecyclePhase.DISCOVERY:
            if missing:
                return f"Still getting to know the user: {', '.join(missing[:2])}."
            return "Still in early discovery; keep curiosity gentle."
        return "Personality is still forming; favor consistency over intensity."

    async def _build_prompt(
        self,
        storage: SoulStorage,
        state: CompanionState,
        topic: str,
    ) -> str:
        nodes = await storage.list_discovery_nodes(limit=4)
        snippets = [f"- {item['topic']}: {item['content']}" for item in nodes[:3]]
        return (
            f"Lifecycle: {state.discovery.lifecycle_phase.value.lower()}\n"
            f"Next topic: {topic}\n"
            f"Interaction count: {state.discovery.interaction_count}\n"
            f"Known discovery notes:\n{chr(10).join(snippets) if snippets else '- none yet'}\n"
            "Write one gentle companion message that invites but does not pressure."
        )

    def _next_phase(
        self,
        discovery: DiscoveryState,
        *,
        now: datetime,
        permanent_patterns: int,
    ) -> SoulLifecyclePhase:
        if discovery.lifecycle_phase is SoulLifecyclePhase.DISCOVERY:
            if _should_form(discovery):
                return SoulLifecyclePhase.FORMING
            return SoulLifecyclePhase.DISCOVERY
        if discovery.lifecycle_phase is SoulLifecyclePhase.FORMING:
            if _should_mature(
                discovery,
                now=now,
                permanent_patterns=permanent_patterns,
            ):
                return SoulLifecyclePhase.MATURE
            return SoulLifecyclePhase.FORMING
        return SoulLifecyclePhase.MATURE


def _detect_topic(text: str) -> tuple[str | None, float]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    if not normalized:
        return None, 0.0

    scores: dict[str, float] = {topic: 0.0 for topic in _DISCOVERY_TOPICS}
    for topic, patterns in _TOPIC_PATTERNS.items():
        for needle in patterns:
            if needle in normalized:
                scores[topic] += 0.25

    if len(normalized.split()) <= 3 and re.fullmatch(r"[\w\-]+", normalized):
        scores["identity"] += 0.45
    if "?" in normalized and any(
        word in normalized for word in ("prefer", "should", "лучше")
    ):
        scores["communication"] += 0.15
    if len(normalized) > 120:
        scores["interests"] += 0.10
        scores["work"] += 0.10

    topic = max(scores, key=scores.get)
    confidence = min(scores[topic], 0.8)
    if confidence < 0.20:
        return None, 0.0
    return topic, confidence


def _lowest_topic(topics: DiscoveryTopicCoverage) -> str | None:
    values = topics.to_dict()
    if not values:
        return None
    return min(values, key=values.get)


def _missing_topics(topics: DiscoveryTopicCoverage) -> list[str]:
    return [name for name, value in topics.to_dict().items() if value < 0.55]


def _should_form(discovery: DiscoveryState) -> bool:
    covered = sum(1 for value in discovery.topics.to_dict().values() if value >= 0.55)
    return discovery.interaction_count >= 20 or covered >= 4


def _should_mature(
    discovery: DiscoveryState,
    *,
    now: datetime,
    permanent_patterns: int,
) -> bool:
    age_days = (
        (now - discovery.first_interaction_at).days
        if discovery.first_interaction_at is not None
        else 0
    )
    return age_days >= 30 or (
        discovery.interaction_count >= 40 and permanent_patterns >= 2
    )


def _compact_content(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip())
    return normalized[:160]
