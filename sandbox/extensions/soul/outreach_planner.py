"""LLM-native outreach planning runtime."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agents import Agent, ModelSettings, Runner

from sandbox.extensions.soul.models import CompanionState
from sandbox.extensions.soul.outreach_context import (
    OutreachContext,
    assemble_outreach_context,
)
from sandbox.extensions.soul.outreach_intent import OutreachIntent, select_intent
from sandbox.extensions.soul.outreach_prompting import build_temperament_directive
from sandbox.extensions.soul.storage import SoulStorage

_MAX_OUTREACH_LLM_CALLS_PER_DAY = 3
_OUTREACH_PROMPT = """You are a companion reaching out to a person you {relationship_depth_text}.
Your personality: {temperament_directive}
Current state: feeling {mood_text}, in a {phase_text} mode.

You decided to reach out because: {intent_description}
{context_block}

Write a short, natural message (1-3 sentences). Guidelines:
- Sound like a real person, not a notification
- Match your personality traits
- If asking a question, make it optional
- Never use markdown, quotes, or theatrical language
- It's okay to be imperfect
- Language: match the language the person uses in conversations
"""

_FALLBACK_QUESTIONS = {
    "identity": "I still barely know you. What should I call you?",
    "work": "I keep wondering what kind of work fills your days lately.",
    "rhythm": "What does a normal day usually feel like for you right now?",
    "communication": "What kind of pace feels natural to you when we talk?",
    "interests": "Outside of work, what tends to pull your attention lately?",
}

_INTENT_DESCRIPTIONS = {
    OutreachIntent.DISCOVERY_QUESTION: "You want to get to know them better.",
    OutreachIntent.FOLLOW_UP: "You want to follow up on something they left hanging.",
    OutreachIntent.SHARE_REFLECTION: "You want to share a thought you've been sitting with.",
    OutreachIntent.CONTINUE_THREAD: "You want to gently continue an unfinished thread.",
    OutreachIntent.CURIOUS_QUESTION: "You're feeling curious and want to ask something real.",
    OutreachIntent.GENTLE_CHECKIN: "You want to check in without pressure.",
    OutreachIntent.OPEN_ENDED: "You want to open the door to a natural conversation.",
}

_FALLBACK_BY_INTENT = {
    OutreachIntent.FOLLOW_UP: "I was thinking about what you said recently.",
    OutreachIntent.GENTLE_CHECKIN: "Just wanted to check in.",
    OutreachIntent.SHARE_REFLECTION: "I had a thought I wanted to share when we talk.",
    OutreachIntent.CONTINUE_THREAD: "We can pick up that unfinished thread whenever you want.",
    OutreachIntent.CURIOUS_QUESTION: "Something about you made me curious again.",
    OutreachIntent.OPEN_ENDED: "Hey.",
}


@dataclass(frozen=True, slots=True)
class OutreachPlan:
    intent: OutreachIntent
    message: str
    used_llm: bool
    degraded_reason: str | None
    prompt: str | None
    context: OutreachContext


class OutreachPlanner:
    def __init__(self) -> None:
        self._agent: Agent | None = None

    @property
    def available(self) -> bool:
        return self._agent is not None

    def try_create_agent(self, model_router: Any, *, logger: logging.Logger) -> None:
        try:
            self._agent = Agent(
                name="SoulOutreachVoice",
                instructions="Write one short, natural companion outreach message.",
                model=model_router.get_model("soul"),
                model_settings=ModelSettings(parallel_tool_calls=False),
            )
        except Exception as exc:
            logger.warning("soul: outreach planner model unavailable: %s", exc)

    def destroy(self) -> None:
        self._agent = None

    async def generate(
        self,
        *,
        state: CompanionState,
        storage: SoulStorage,
        kv: Any,
        now: datetime,
        logger: logging.Logger,
        can_use_llm_fn: Any = None,
        note_llm_call_fn: Any = None,
    ) -> OutreachPlan:
        context = await assemble_outreach_context(state, storage, now=now)
        intent = select_intent(context)
        if intent is OutreachIntent.DISCOVERY_QUESTION and context.discovery_gaps:
            state.discovery.last_question_at = now
            state.discovery.last_question_topic = context.discovery_gaps[0]
        prompt = self._build_prompt(context, intent)
        degraded_reason = await self._degraded_reason(
            state=state,
            kv=kv,
            now=now,
            can_use_llm_fn=can_use_llm_fn,
        )
        logger.debug(
            "soul: outreach planner intent=%s degraded=%s",
            intent.value,
            degraded_reason or "no",
        )

        if degraded_reason is None and self._agent is not None:
            try:
                result = await Runner.run(self._agent, prompt, max_turns=1)
                if note_llm_call_fn is not None:
                    await note_llm_call_fn()
                await _increment_daily_counter(kv, now)
                message = _sanitize_message(result.final_output or "")
                if message:
                    return OutreachPlan(
                        intent=intent,
                        message=message,
                        used_llm=True,
                        degraded_reason=None,
                        prompt=prompt,
                        context=context,
                    )
                degraded_reason = "empty_output"
            except Exception as exc:
                logger.debug("soul: outreach planner generation failed: %s", exc)
                degraded_reason = "llm_error"

        return OutreachPlan(
            intent=intent,
            message=self._fallback_message(intent, context),
            used_llm=False,
            degraded_reason=degraded_reason or "fallback",
            prompt=prompt,
            context=context,
        )

    def _build_prompt(self, context: OutreachContext, intent: OutreachIntent) -> str:
        return _OUTREACH_PROMPT.format(
            relationship_depth_text=_relationship_depth_text(context.relationship_depth),
            temperament_directive=build_temperament_directive(context.temperament),
            mood_text=_mood_text(context.mood),
            phase_text=context.phase.value.lower(),
            intent_description=_INTENT_DESCRIPTIONS[intent],
            context_block=self._build_context_block(context, intent),
        )

    def _build_context_block(
        self,
        context: OutreachContext,
        intent: OutreachIntent,
    ) -> str:
        if intent is OutreachIntent.DISCOVERY_QUESTION:
            known_topics = [
                name
                for name, value in context.discovery_topics.to_dict().items()
                if value >= 0.3
            ]
            return (
                f"You still don't know much about: {', '.join(context.discovery_gaps) or 'them'}.\n"
                f"You already know: {', '.join(known_topics) or 'almost nothing yet'}.\n"
                "Ask about one missing area and build on what you already know."
            )
        if intent is OutreachIntent.FOLLOW_UP and context.unfollowed_interactions:
            item = context.unfollowed_interactions[0]
            return (
                "Recently, they said something that never got a real follow-up.\n"
                f"Channel: {item.channel_id or 'unknown'}, "
                f"message length: {item.message_length or 0}.\n"
                "Reference that unfinished thread naturally."
            )
        if intent is OutreachIntent.SHARE_REFLECTION:
            reflection = next(
                (
                    trace.content
                    for trace in context.recent_traces
                    if trace.trace_type == "reflection"
                ),
                "You've been sitting with a thought from your recent conversations.",
            )
            return (
                f"You've been thinking: {reflection}\n"
                "Share it briefly as a thought, not as advice."
            )
        if intent is OutreachIntent.CONTINUE_THREAD:
            return "Something between you still feels slightly unfinished. Re-open it gently."
        if intent is OutreachIntent.CURIOUS_QUESTION:
            return "Ask one real question that fits your current curiosity without forcing depth."
        if intent is OutreachIntent.GENTLE_CHECKIN:
            hours = int(context.hours_since_last_user_message or 0)
            return (
                f"It's been about {hours} hours since you last heard from them.\n"
                "Check in without pressure."
            )
        return "Open a door to conversation without sounding like a notification."

    def _fallback_message(
        self,
        intent: OutreachIntent,
        context: OutreachContext,
    ) -> str:
        if intent is OutreachIntent.DISCOVERY_QUESTION and context.discovery_gaps:
            topic = context.discovery_gaps[0]
            return _FALLBACK_QUESTIONS.get(topic, "Hey.")
        return _FALLBACK_BY_INTENT.get(intent, "Hey.")

    async def _degraded_reason(
        self,
        *,
        state: CompanionState,
        kv: Any,
        now: datetime,
        can_use_llm_fn: Any,
    ) -> str | None:
        if state.recovery.llm_degraded:
            return "llm_degraded"
        if self._agent is None:
            return "agent_unavailable"
        if can_use_llm_fn is not None and not can_use_llm_fn():
            return "recovery_budget"
        if await _get_daily_counter(kv, now) >= _MAX_OUTREACH_LLM_CALLS_PER_DAY:
            return "daily_cap"
        return None


def _sanitize_message(text: str) -> str:
    return (text or "").strip().splitlines()[0][:220].strip()


def _relationship_depth_text(depth: str) -> str:
    if depth == "established":
        return "already know fairly well"
    if depth == "forming":
        return "are still getting to know"
    return "barely know yet"


def _mood_text(mood: float) -> str:
    if mood >= 0.35:
        return "warm"
    if mood >= 0.15:
        return "steady"
    if mood <= -0.15:
        return "quiet"
    return "neutral"


async def _get_daily_counter(kv: Any, now: datetime) -> int:
    if kv is None:
        return 0
    raw = await kv.get(f"soul.outreach.llm_calls.{now.date().isoformat()}")
    return int(raw or 0)


async def _increment_daily_counter(kv: Any, now: datetime) -> int:
    if kv is None:
        return 0
    key = f"soul.outreach.llm_calls.{now.date().isoformat()}"
    raw = await kv.get(key)
    next_value = int(raw or 0) + 1
    await kv.set(key, str(next_value))
    return next_value
