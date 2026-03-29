import logging
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
    ) -> list[dict]:
        return []

    async def list_traces_since(
        self,
        since: datetime,
        *,
        trace_types: tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> list[dict]:
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
        return []

    async def get_daily_metrics(self, metric_date: date) -> dict:
        return {"date": metric_date.isoformat()}


class FakeKV:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._store = dict(initial or {})

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value


def _base_state() -> CompanionState:
    state = CompanionState()
    state.discovery.lifecycle_phase = SoulLifecyclePhase.DISCOVERY
    state.discovery.topics.identity = 0.1
    state.discovery.topics.work = 0.9
    return state


async def test_outreach_planner_uses_discovery_fallback_when_degraded() -> None:
    planner = OutreachPlanner()
    state = _base_state()
    state.recovery.llm_degraded = True

    plan = await planner.generate(
        state=state,
        storage=PlannerStorage(),
        kv=FakeKV(),
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
        logger=logging.getLogger("test"),
    )

    assert plan.intent is OutreachIntent.DISCOVERY_QUESTION
    assert plan.used_llm is False
    assert plan.degraded_reason == "llm_degraded"
    assert plan.message == "I still barely know you. What should I call you?"


async def test_outreach_planner_respects_daily_llm_cap(monkeypatch) -> None:
    planner = OutreachPlanner()
    planner._agent = object()  # type: ignore[assignment]
    state = _base_state()
    kv = FakeKV({"soul.outreach.llm_calls.2026-03-29": "3"})

    plan = await planner.generate(
        state=state,
        storage=PlannerStorage(),
        kv=kv,
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
        logger=logging.getLogger("test"),
    )

    assert plan.used_llm is False
    assert plan.degraded_reason == "daily_cap"


async def test_outreach_planner_generates_llm_message(monkeypatch) -> None:
    planner = OutreachPlanner()
    planner._agent = object()  # type: ignore[assignment]
    state = _base_state()
    state.discovery.lifecycle_phase = SoulLifecyclePhase.FORMING
    state.discovery.topics.identity = 0.8
    state.discovery.topics.rhythm = 0.8
    state.discovery.topics.communication = 0.8
    state.discovery.topics.interests = 0.8
    kv = FakeKV()
    noted: list[datetime] = []

    class FakeResult:
        final_output = "I've been thinking about what you said about purpose.\nExtra"

    async def fake_run(agent, prompt, max_turns):
        assert max_turns == 1
        assert "Your personality:" in prompt
        assert "You want to share a thought you've been sitting with." in prompt
        assert "Never invent specifics you don't have" in prompt
        return FakeResult()

    monkeypatch.setattr(
        "sandbox.extensions.soul.outreach_planner.Runner.run",
        fake_run,
    )

    plan = await planner.generate(
        state=state,
        storage=PlannerStorage(),
        kv=kv,
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
        logger=logging.getLogger("test"),
        note_llm_call_fn=lambda: _note_call(noted),
    )

    assert plan.used_llm is True
    assert plan.intent is OutreachIntent.SHARE_REFLECTION
    assert plan.message == "I've been thinking about what you said about purpose."
    assert kv._store["soul.outreach.llm_calls.2026-03-29"] == "1"
    assert len(noted) == 1


async def _note_call(bucket: list[datetime]) -> None:
    bucket.append(datetime.now(UTC))
