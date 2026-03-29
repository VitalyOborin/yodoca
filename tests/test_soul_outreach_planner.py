from datetime import UTC, date, datetime

from sandbox.extensions.soul.models import CompanionState, SoulLifecyclePhase
from sandbox.extensions.soul.outreach_intent import OutreachIntent
from sandbox.extensions.soul.outreach_planner import OutreachPlanner


class PlannerStorage:
    async def list_recent_interactions(self, limit: int = 10) -> list[dict]:
        return [
            {
                "id": 1,
                "direction": "inbound",
                "channel_id": "cli_channel",
                "outreach_result": None,
                "message_length": 120,
                "openness_signal": 0.7,
                "response_delay_s": None,
                "created_at": "2026-03-29T09:00:00+00:00",
            }
        ]

    async def list_unfollowed_interactions(
        self,
        *,
        limit: int = 5,
        follow_up_window_hours: int = 4,
        now: object = None,
    ) -> list[dict]:
        del limit, follow_up_window_hours, now
        return []

    async def list_traces_since(
        self,
        since: datetime,
        *,
        trace_types: tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        del since, trace_types, limit
        return [
            {
                "id": 5,
                "trace_type": "reflection",
                "phase": "REFLECTIVE",
                "content": "The user keeps returning to purpose.",
                "payload_json": None,
                "created_at": "2026-03-29T08:00:00+00:00",
            }
        ]

    async def list_discovery_nodes(self, *, limit: int = 20) -> list[dict]:
        del limit
        return [
            {
                "id": 2,
                "topic": "work",
                "content": "Builds agent runtimes.",
                "confidence": 0.8,
                "created_at": "2026-03-29T07:00:00+00:00",
            }
        ]

    async def list_relationship_patterns(
        self,
        *,
        permanent_only: bool = False,
    ) -> list[dict]:
        del permanent_only
        return []

    async def get_daily_metrics(self, metric_date: date) -> dict:
        return {"date": metric_date.isoformat()}


def _base_state() -> CompanionState:
    state = CompanionState()
    state.discovery.lifecycle_phase = SoulLifecyclePhase.DISCOVERY
    state.discovery.topics.identity = 0.1
    state.discovery.topics.work = 0.9
    return state


async def test_outreach_planner_builds_directive_for_discovery_question() -> None:
    planner = OutreachPlanner()

    plan = await planner.generate(
        state=_base_state(),
        storage=PlannerStorage(),
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
    )

    assert plan.intent is OutreachIntent.DISCOVERY_QUESTION
    assert "initiating contact proactively" in plan.directive
    assert "get to know them better" in plan.directive
    assert "identity" in plan.directive
    assert plan.fallback_text == "I still barely know you. What should I call you?"


async def test_outreach_planner_builds_directive_for_reflection() -> None:
    planner = OutreachPlanner()
    state = _base_state()
    state.discovery.lifecycle_phase = SoulLifecyclePhase.FORMING
    state.discovery.topics.identity = 0.8
    state.discovery.topics.rhythm = 0.8
    state.discovery.topics.communication = 0.8
    state.discovery.topics.interests = 0.8

    plan = await planner.generate(
        state=state,
        storage=PlannerStorage(),
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
    )

    assert plan.intent is OutreachIntent.SHARE_REFLECTION
    assert "share a thought you've been sitting with" in plan.directive
    assert "The user keeps returning to purpose." in plan.directive
    assert plan.fallback_text == "I had a thought I wanted to share when we talk."


async def test_outreach_planner_returns_discovery_topic_without_mutating_state() -> None:
    planner = OutreachPlanner()
    state = _base_state()
    original_topic = state.discovery.last_question_topic

    plan = await planner.generate(
        state=state,
        storage=PlannerStorage(),
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
    )

    assert plan.discovery_question_topic == "identity"
    assert state.discovery.last_question_topic == original_topic
