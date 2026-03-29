"""Deterministic outreach intent selection."""

from __future__ import annotations

from enum import StrEnum

from sandbox.extensions.soul.models import Phase, SoulLifecyclePhase
from sandbox.extensions.soul.outreach_context import OutreachContext


class OutreachIntent(StrEnum):
    DISCOVERY_QUESTION = "discovery_question"
    FOLLOW_UP = "follow_up"
    SHARE_REFLECTION = "share_reflection"
    CONTINUE_THREAD = "continue_thread"
    CURIOUS_QUESTION = "curious_question"
    GENTLE_CHECKIN = "gentle_checkin"
    OPEN_ENDED = "open_ended"


def select_intent(context: OutreachContext) -> OutreachIntent:
    if _should_ask_discovery_question(context):
        return OutreachIntent.DISCOVERY_QUESTION
    if _should_follow_up(context):
        return OutreachIntent.FOLLOW_UP
    if _has_recent_reflection(context):
        return OutreachIntent.SHARE_REFLECTION
    if _should_continue_thread(context):
        return OutreachIntent.CONTINUE_THREAD
    if _should_ask_curious_question(context):
        return OutreachIntent.CURIOUS_QUESTION
    if _should_gently_check_in(context):
        return OutreachIntent.GENTLE_CHECKIN
    return OutreachIntent.OPEN_ENDED


def _should_ask_discovery_question(context: OutreachContext) -> bool:
    return (
        context.lifecycle is SoulLifecyclePhase.DISCOVERY
        and len(context.discovery_gaps) > 0
    )


def _should_follow_up(context: OutreachContext) -> bool:
    """User said something hours ago that was never followed up (storage anti-join).
    Requires existing knowledge (discovery nodes or traces) to reference."""
    if not context.unfollowed_interactions:
        return False
    return bool(context.discovery_nodes or context.recent_traces)


def _has_recent_reflection(context: OutreachContext) -> bool:
    return any(trace.trace_type == "reflection" for trace in context.recent_traces)


def _should_continue_thread(context: OutreachContext) -> bool:
    """Most recent interaction is inbound (user spoke last) or last outreach was
    ignored/missed — re-open the conversation.  Unlike follow_up, this doesn't
    require an unfollowed-interaction window from storage; it fires on recency
    or on a failed prior outreach attempt."""
    if context.recent_interactions:
        if context.recent_interactions[0].direction == "inbound":
            return True
    if context.last_outreach_result is None:
        return False
    return context.last_outreach_result.value in {"ignored", "timing_miss"}


def _should_ask_curious_question(context: OutreachContext) -> bool:
    return (
        context.phase is Phase.CURIOUS
        and context.lifecycle is not SoulLifecyclePhase.DISCOVERY
        and context.relationship_depth != "new"
    )


def _should_gently_check_in(context: OutreachContext) -> bool:
    if context.phase is Phase.CARE:
        return True
    if context.hours_since_last_user_message is None:
        return False
    return context.hours_since_last_user_message > 48.0
