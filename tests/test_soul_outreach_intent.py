from datetime import UTC, datetime

from sandbox.extensions.soul.models import (
    CompanionState,
    OutreachResult,
    Phase,
    SoulLifecyclePhase,
)
from sandbox.extensions.soul.outreach_context import assemble_outreach_context
from sandbox.extensions.soul.outreach_intent import OutreachIntent, select_intent


class IntentStorage:
    def __init__(
        self,
        *,
        recent_interactions: list[dict] | None = None,
        unfollowed_interactions: list[dict] | None = None,
        recent_traces: list[dict] | None = None,
        discovery_nodes: list[dict] | None = None,
    ) -> None:
        self._recent_interactions = recent_interactions or []
        self._unfollowed_interactions = unfollowed_interactions or []
        self._recent_traces = recent_traces or []
        self._discovery_nodes = discovery_nodes or []

    async def list_recent_interactions(self, limit: int = 10) -> list[dict]:
        return self._recent_interactions[:limit]

    async def list_unfollowed_interactions(
        self,
        *,
        limit: int = 5,
        follow_up_window_hours: int = 4,
    ) -> list[dict]:
        return self._unfollowed_interactions[:limit]

    async def list_traces_since(
        self,
        since: datetime,
        *,
        trace_types: tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        return self._recent_traces[:limit]

    async def list_discovery_nodes(self, *, limit: int = 20) -> list[dict]:
        return self._discovery_nodes[:limit]

    async def list_relationship_patterns(
        self,
        *,
        permanent_only: bool = False,
    ) -> list[dict]:
        return []

    async def get_daily_metrics(self, metric_date) -> dict:
        return {"date": metric_date.isoformat()}


def _base_state() -> CompanionState:
    state = CompanionState()
    state.homeostasis.current_phase = Phase.AMBIENT
    state.discovery.lifecycle_phase = SoulLifecyclePhase.FORMING
    state.discovery.interaction_count = 10
    state.discovery.topics.identity = 0.9
    state.discovery.topics.work = 0.8
    state.discovery.topics.rhythm = 0.8
    state.discovery.topics.communication = 0.8
    state.discovery.topics.interests = 0.8
    return state


async def _context_from(
    *,
    state: CompanionState | None = None,
    storage: IntentStorage | None = None,
) -> object:
    return await assemble_outreach_context(
        state or _base_state(),
        storage or IntentStorage(),
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
    )


async def test_select_intent_prefers_discovery_question() -> None:
    state = _base_state()
    state.discovery.lifecycle_phase = SoulLifecyclePhase.DISCOVERY
    state.discovery.topics.identity = 0.1
    context = await _context_from(state=state)

    assert select_intent(context) is OutreachIntent.DISCOVERY_QUESTION


async def test_select_intent_prefers_follow_up_over_other_branches() -> None:
    storage = IntentStorage(
        unfollowed_interactions=[
            {
                "id": 1,
                "direction": "inbound",
                "channel_id": "cli_channel",
                "outreach_result": None,
                "message_length": 120,
                "openness_signal": 0.7,
                "response_delay_s": None,
                "created_at": "2026-03-29T08:00:00+00:00",
            }
        ],
        discovery_nodes=[
            {
                "id": 4,
                "topic": "work",
                "content": "Builds agent runtimes.",
                "confidence": 0.7,
                "created_at": "2026-03-29T07:00:00+00:00",
            }
        ],
        recent_traces=[
            {
                "id": 7,
                "trace_type": "reflection",
                "phase": "REFLECTIVE",
                "content": "Thinking about work.",
                "payload_json": None,
                "created_at": "2026-03-29T09:00:00+00:00",
            }
        ],
    )

    context = await _context_from(storage=storage)

    assert select_intent(context) is OutreachIntent.FOLLOW_UP


async def test_select_intent_chooses_share_reflection() -> None:
    storage = IntentStorage(
        recent_traces=[
            {
                "id": 7,
                "trace_type": "reflection",
                "phase": "REFLECTIVE",
                "content": "Thinking about purpose.",
                "payload_json": None,
                "created_at": "2026-03-29T09:00:00+00:00",
            }
        ]
    )

    context = await _context_from(storage=storage)

    assert select_intent(context) is OutreachIntent.SHARE_REFLECTION


async def test_select_intent_chooses_continue_thread_for_unanswered_inbound() -> None:
    storage = IntentStorage(
        recent_interactions=[
            {
                "id": 5,
                "direction": "inbound",
                "channel_id": "cli_channel",
                "outreach_result": None,
                "message_length": 80,
                "openness_signal": 0.5,
                "response_delay_s": None,
                "created_at": "2026-03-29T11:30:00+00:00",
            }
        ]
    )

    context = await _context_from(storage=storage)

    assert select_intent(context) is OutreachIntent.CONTINUE_THREAD


async def test_select_intent_chooses_curious_question() -> None:
    state = _base_state()
    state.homeostasis.current_phase = Phase.CURIOUS
    context = await _context_from(state=state)

    assert select_intent(context) is OutreachIntent.CURIOUS_QUESTION


async def test_select_intent_chooses_gentle_checkin() -> None:
    state = _base_state()
    state.user_presence.last_interaction_at = datetime(
        2026, 3, 26, 9, 0, tzinfo=UTC
    )
    context = await _context_from(state=state)

    assert select_intent(context) is OutreachIntent.GENTLE_CHECKIN


async def test_select_intent_falls_back_to_open_ended() -> None:
    state = _base_state()
    storage = IntentStorage(
        recent_interactions=[
            {
                "id": 6,
                "direction": "outbound",
                "channel_id": "cli_channel",
                "outreach_result": "response",
                "message_length": 25,
                "openness_signal": None,
                "response_delay_s": None,
                "created_at": "2026-03-29T11:30:00+00:00",
            }
        ]
    )
    state.initiative.last_outreach_result = OutreachResult.RESPONSE

    context = await _context_from(state=state, storage=storage)

    assert select_intent(context) is OutreachIntent.OPEN_ENDED
