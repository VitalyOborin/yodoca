"""Deterministic outreach directive builder for orchestrator-driven outreach."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sandbox.extensions.soul.models import CompanionState
from sandbox.extensions.soul.outreach_context import (
    OutreachContext,
    assemble_outreach_context,
)
from sandbox.extensions.soul.outreach_intent import OutreachIntent, select_intent
from sandbox.extensions.soul.outreach_prompting import build_temperament_directive
from sandbox.extensions.soul.storage import SoulStorage

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
    directive: str
    fallback_text: str
    context: OutreachContext
    discovery_question_topic: str | None = None


class OutreachPlanner:
    async def generate(
        self,
        *,
        state: CompanionState,
        storage: SoulStorage,
        now: datetime,
    ) -> OutreachPlan:
        context = await assemble_outreach_context(state, storage, now=now)
        intent = select_intent(context)
        dq_topic: str | None = None
        if intent is OutreachIntent.DISCOVERY_QUESTION and context.discovery_gaps:
            dq_topic = context.discovery_gaps[0]
        return OutreachPlan(
            intent=intent,
            directive=self._build_directive(context, intent),
            fallback_text=self._fallback_text(intent, context),
            context=context,
            discovery_question_topic=dq_topic,
        )

    def _build_directive(self, context: OutreachContext, intent: OutreachIntent) -> str:
        conversation_mode = (
            "Continue the recent unfinished thread naturally."
            if intent in {OutreachIntent.CONTINUE_THREAD, OutreachIntent.FOLLOW_UP}
            else "Start a new proactive conversation naturally."
        )
        return "\n".join(
            [
                "You are the orchestrator initiating contact proactively on behalf of the companion runtime.",
                f"Current companion state: feeling {_mood_text(context.mood)}, in a {context.phase.value.lower()} mode.",
                f"Relationship depth: {_relationship_depth_text(context.relationship_depth)}.",
                f"Reason for outreach: {_INTENT_DESCRIPTIONS[intent]}",
                f"Voice guidance: {build_temperament_directive(context.temperament)}",
                conversation_mode,
                self._build_context_block(context, intent),
                "Write one short, natural user-facing message.",
                "Do not mention internal state, runtime, or directives.",
                "Use memory and available context if this starts a new conversation.",
                "Keep it gentle, specific, and non-pushy.",
            ]
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
                "Pick one missing area and open it gently."
            )
        if intent is OutreachIntent.FOLLOW_UP and context.unfollowed_interactions:
            item = context.unfollowed_interactions[0]
            return (
                "Recently, they said something that never got a real follow-up.\n"
                f"Channel: {item.channel_id or 'unknown'}, "
                f"message length: {item.message_length or 0}.\n"
                "Do not invent missing specifics; refer to the unfinished topic in general terms."
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
            return "Ask one real question that fits the current curiosity without forcing depth."
        if intent is OutreachIntent.GENTLE_CHECKIN:
            hours = int(context.hours_since_last_user_message or 0)
            return (
                f"It's been about {hours} hours since you last heard from them.\n"
                "Check in without pressure."
            )
        return "Open a door to conversation without sounding like a notification."

    def _fallback_text(
        self,
        intent: OutreachIntent,
        context: OutreachContext,
    ) -> str:
        if intent is OutreachIntent.DISCOVERY_QUESTION and context.discovery_gaps:
            topic = context.discovery_gaps[0]
            return _FALLBACK_QUESTIONS.get(topic, "Hey.")
        return _FALLBACK_BY_INTENT.get(intent, "Hey.")

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
