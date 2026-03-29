from datetime import UTC, date, datetime

from sandbox.extensions.soul.models import (
    CompanionState,
    OutreachResult,
    Phase,
    SoulLifecyclePhase,
)
from sandbox.extensions.soul.outreach_context import assemble_outreach_context


class FakeOutreachStorage:
    def __init__(self) -> None:
        self.metric_date: date | None = None

    async def list_recent_interactions(self, limit: int = 10) -> list[dict]:
        assert limit == 10
        return [
            {
                "id": 4,
                "direction": "outbound",
                "channel_id": "cli_channel",
                "outreach_result": "response",
                "message_length": 52,
                "openness_signal": None,
                "response_delay_s": None,
                "created_at": "2026-03-29T11:00:00+00:00",
            },
            {
                "id": 3,
                "direction": "inbound",
                "channel_id": "cli_channel",
                "outreach_result": None,
                "message_length": 140,
                "openness_signal": 0.72,
                "response_delay_s": 120,
                "created_at": "2026-03-29T10:30:00+00:00",
            },
        ]

    async def list_unfollowed_interactions(
        self,
        *,
        limit: int = 5,
        follow_up_window_hours: int = 4,
        now: object = None,
    ) -> list[dict]:
        assert limit == 5
        assert follow_up_window_hours == 4
        return [
            {
                "id": 2,
                "direction": "inbound",
                "channel_id": "telegram_channel",
                "outreach_result": None,
                "message_length": 90,
                "openness_signal": 0.64,
                "response_delay_s": None,
                "created_at": "2026-03-29T06:00:00+00:00",
            }
        ]

    async def list_traces_since(
        self,
        since: datetime,
        *,
        trace_types: tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        assert trace_types == ("reflection", "exploration")
        assert limit == 8
        return [
            {
                "id": 8,
                "trace_type": "reflection",
                "phase": "REFLECTIVE",
                "content": "The user keeps returning to purpose.",
                "payload_json": None,
                "created_at": "2026-03-29T09:00:00+00:00",
            }
        ]

    async def list_discovery_nodes(self, *, limit: int = 20) -> list[dict]:
        assert limit == 8
        return [
            {
                "id": 7,
                "topic": "work",
                "content": "Builds agent runtimes.",
                "confidence": 0.7,
                "created_at": "2026-03-28T18:00:00+00:00",
            }
        ]

    async def list_relationship_patterns(
        self,
        *,
        permanent_only: bool = False,
    ) -> list[dict]:
        assert permanent_only is False
        return [
            {
                "pattern_key": "evening-architecture",
                "pattern_type": "ritual",
                "content": "Architecture talks in the evening.",
                "repetition_count": 3,
                "confidence": 0.81,
                "is_permanent": 1,
                "updated_at": "2026-03-29T08:00:00+00:00",
            }
        ]

    async def get_daily_metrics(self, metric_date: date) -> dict:
        self.metric_date = metric_date
        return {
            "date": metric_date.isoformat(),
            "outreach_attempts": 1,
            "message_count": 4,
        }


async def test_assemble_outreach_context_builds_expected_snapshot() -> None:
    state = CompanionState()
    state.homeostasis.current_phase = Phase.CURIOUS
    state.discovery.lifecycle_phase = SoulLifecyclePhase.FORMING
    state.discovery.interaction_count = 12
    state.discovery.topics.identity = 0.15
    state.discovery.topics.work = 0.65
    state.discovery.topics.rhythm = 0.20
    state.discovery.topics.communication = 0.48
    state.discovery.topics.interests = 0.25
    state.initiative.last_outreach_result = OutreachResult.RESPONSE
    state.user_presence.estimated_availability = 0.71
    state.user_presence.last_interaction_at = datetime(2026, 3, 29, 10, 30, tzinfo=UTC)

    storage = FakeOutreachStorage()
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)

    context = await assemble_outreach_context(state, storage, now=now)

    assert context.phase is Phase.CURIOUS
    assert context.lifecycle is SoulLifecyclePhase.FORMING
    assert context.relationship_depth == "forming"
    assert context.discovery_gaps == ["identity", "rhythm", "interests"]
    assert context.last_outreach_result is OutreachResult.RESPONSE
    assert context.hours_since_last_user_message == 1.5
    assert context.estimated_availability == 0.71
    assert len(context.recent_interactions) == 2
    assert context.recent_interactions[1].direction == "inbound"
    assert len(context.unfollowed_interactions) == 1
    assert context.unfollowed_interactions[0].channel_id == "telegram_channel"
    assert len(context.recent_traces) == 1
    assert context.recent_traces[0].trace_type == "reflection"
    assert len(context.discovery_nodes) == 1
    assert context.discovery_nodes[0].topic == "work"
    assert len(context.relationship_patterns) == 1
    assert context.daily_metrics is not None
    assert storage.metric_date == date(2026, 3, 29)


async def test_assemble_outreach_context_falls_back_to_presence_timestamp() -> None:
    state = CompanionState()
    state.homeostasis.current_phase = Phase.AMBIENT
    state.discovery.lifecycle_phase = SoulLifecyclePhase.MATURE
    state.user_presence.last_interaction_at = datetime(2026, 3, 29, 7, 0, tzinfo=UTC)

    class NoInboundStorage(FakeOutreachStorage):
        async def list_recent_interactions(self, limit: int = 10) -> list[dict]:
            return [
                {
                    "id": 10,
                    "direction": "outbound",
                    "channel_id": "cli_channel",
                    "outreach_result": "ignored",
                    "message_length": 24,
                    "openness_signal": None,
                    "response_delay_s": None,
                    "created_at": "2026-03-29T08:00:00+00:00",
                }
            ]

        async def list_relationship_patterns(
            self,
            *,
            permanent_only: bool = False,
        ) -> list[dict]:
            return []

    context = await assemble_outreach_context(
        state,
        NoInboundStorage(),
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
    )

    assert context.relationship_depth == "established"
    assert context.hours_since_last_user_message == 5.0
