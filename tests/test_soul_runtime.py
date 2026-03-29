import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from conftest import FakeSoulContext

from sandbox.extensions.soul.main import SoulExtension
from sandbox.extensions.soul.models import Phase, PresenceState, SoulLifecyclePhase


async def test_inner_tick_emits_phase_and_presence_events(tmp_path: Path) -> None:
    context = FakeSoulContext(
        tmp_path,
        {
            "tick_interval_seconds": 30,
            "persist_interval_seconds": 300,
        },
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.presence = PresenceState.SILENT
    ext._state.homeostasis.current_phase = Phase.AMBIENT
    ext._state.homeostasis.curiosity = 0.9
    ext._state.homeostasis.phase_entered_at = datetime.now(UTC) - timedelta(minutes=10)
    ext._state.homeostasis.last_tick_at = datetime.now(UTC) - timedelta(minutes=10)

    tick_now = datetime.now(UTC)
    await ext._run_one_tick(now=tick_now)

    assert ext._state.homeostasis.current_phase is Phase.CURIOUS
    assert ext._state.presence is PresenceState.PLAYFUL
    assert [topic for topic, _ in context.events] == [
        "companion.phase.changed",
        "companion.presence.updated",
    ]

    restored = await ext._storage.load_state() if ext._storage else None
    assert restored is not None
    assert restored.homeostasis.current_phase is Phase.CURIOUS
    assert restored.tick_count == 1


async def test_initialize_wires_router_and_event_bus_subscriptions(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()

    await ext.initialize(context)

    assert "user_message" in context.router_subscriptions
    assert "agent_response" in context.router_subscriptions
    assert "thread.completed" in context.bus_subscriptions


async def test_run_background_advances_ticks_until_stopped(tmp_path: Path) -> None:
    context = FakeSoulContext(
        tmp_path,
        {
            "tick_interval_seconds": 0.01,
            "persist_interval_seconds": 0.01,
        },
    )
    ext = SoulExtension()
    await ext.initialize(context)
    await ext.start()

    task = asyncio.create_task(ext.run_background())
    await asyncio.sleep(0.05)
    await ext.stop()
    await asyncio.wait_for(task, timeout=0.2)

    assert ext._state is not None
    assert ext._state.tick_count > 0
    assert ext._last_tick_finished_at is not None
    assert ext.health_check() is True


async def test_health_check_fails_for_stale_heartbeat(tmp_path: Path) -> None:
    context = FakeSoulContext(
        tmp_path,
        {
            "tick_interval_seconds": 10,
            "persist_interval_seconds": 60,
        },
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._started = True
    ext._last_tick_started_at = datetime.now(UTC) - timedelta(seconds=25)

    assert ext.health_check() is False


async def test_health_check_uses_recent_state_tick_when_loop_idle(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(
        tmp_path,
        {
            "tick_interval_seconds": 10,
            "persist_interval_seconds": 60,
        },
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._last_tick_started_at = None
    ext._state.homeostasis.last_tick_at = datetime.now(UTC) - timedelta(seconds=5)

    assert ext.health_check() is True


async def test_user_message_updates_perception_and_social_hunger(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.homeostasis.social_hunger = 0.6
    ext._last_agent_response_at = datetime.now(UTC) - timedelta(minutes=20)

    await ext._on_user_message({"text": "ok...", "channel": object()})

    assert ext._state.homeostasis.social_hunger < 0.6
    assert ext._state.perception.withdrawal_signal > 0.1
    assert ext._last_user_message_at is not None
    assert ext._state.user_presence.estimated_availability >= 0.3


async def test_thresholded_trace_policy_records_meaningful_events(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.presence = PresenceState.SILENT
    ext._state.homeostasis.current_phase = Phase.AMBIENT
    ext._state.homeostasis.curiosity = 0.95
    ext._state.homeostasis.phase_entered_at = datetime.now(UTC) - timedelta(minutes=10)
    ext._state.homeostasis.last_tick_at = datetime.now(UTC) - timedelta(minutes=10)
    await ext._run_one_tick(now=datetime.now(UTC))
    await ext._on_user_message(
        {
            "text": "I am really tired and not very talkative today...",
            "channel": object(),
        }
    )

    with sqlite3.connect(context.data_dir / "soul.db") as conn:
        rows = conn.execute("SELECT trace_type FROM traces ORDER BY id ASC").fetchall()

    trace_types = [row[0] for row in rows]
    assert "phase_transition" in trace_types
    assert "interaction" in trace_types


async def test_context_provider_returns_compact_note(tmp_path: Path) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.perception.fatigue_signal = 0.7
    ext._state.presence = PresenceState.WARM
    ext._state.mood = 0.4

    result = await ext.get_context("hello", object())

    assert result is not None
    assert "phase:" in result
    assert "lifecycle:" in result
    assert "presence:" in result
    assert "mood: warm" in result
    assert "User seems tired; be brief and present." in result
    assert len(result.split()) < 80


async def test_context_provider_adds_relationship_note_when_trend_is_clear(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._storage is not None
    start = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    for day in range(8):
        now = start + timedelta(days=day)
        await ext._storage.append_interaction(
            direction="inbound",
            channel_id="cli_channel",
            message_length=40 + (day * 12),
            openness_signal=0.2 + (day * 0.08),
            created_at=now,
        )
        await ext._storage.append_interaction(
            direction="outbound",
            channel_id="cli_channel",
            message_length=20,
            created_at=now + timedelta(minutes=5),
        )

    result = await ext.get_context("hello", object())

    assert result is not None
    assert "relationship:" in result


async def test_tool_snapshot_exposes_runtime_state(tmp_path: Path) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.homeostasis.current_phase = Phase.REFLECTIVE
    ext._state.homeostasis.phase_entered_at = datetime.now(UTC) - timedelta(minutes=7)
    ext._state.tick_count = 5
    ext._state.mood = 0.2

    snapshot = ext._build_state_snapshot()

    assert snapshot.success is True
    assert snapshot.phase == "REFLECTIVE"
    assert snapshot.tick_count == 5
    assert snapshot.time_in_phase_seconds >= 420
    assert "curiosity" in snapshot.drives
    assert "daily_budget" in snapshot.initiative
    assert "estimated_availability" in snapshot.user_presence
    assert snapshot.discovery["lifecycle_phase"] == "DISCOVERY"
    assert isinstance(snapshot.channels, list)
    assert len(ext.get_tools()) == 3


async def test_metrics_snapshot_reports_context_and_relationship_trends(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._storage is not None
    start = datetime.now(UTC) - timedelta(days=6)
    for day in range(7):
        now = start + timedelta(days=day)
        await ext._storage.append_interaction(
            direction="inbound",
            channel_id="cli_channel",
            message_length=48 + (day * 10),
            openness_signal=0.2 + (day * 0.07),
            created_at=now,
        )
        await ext._storage.upsert_daily_metrics(
            now.date(),
            outreach_attempts=1,
            outreach_responses=1 if day < 2 else 0,
            outreach_ignored=1 if day >= 4 else 0,
            context_words_avg=16 + day,
            perception_corrections=1,
        )

    snapshot = await ext._build_metrics_snapshot()

    assert snapshot.success is True
    assert snapshot.current_context_words > 0
    assert snapshot.context_words_avg_7d > 0
    assert snapshot.perception_corrections_7d == 7
    assert "attempts" in snapshot.outreach_quality_7d


async def test_transparency_snapshot_exposes_raw_state_and_recent_artifacts(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    assert ext._storage is not None
    now = datetime.now(UTC)
    await ext._storage.append_trace(
        trace_type="recovery",
        phase=Phase.AMBIENT.value,
        content="Recovery trace",
        created_at=now,
    )
    await ext._storage.append_discovery_node(
        topic="work",
        content="User builds AI runtimes.",
        confidence=0.7,
        source_json=None,
        created_at=now,
    )
    await ext._storage.append_interaction(
        direction="inbound",
        channel_id="telegram_channel",
        message_length=32,
        created_at=now,
    )
    snapshot = await ext._build_transparency_snapshot()

    assert snapshot.success is True
    assert "\"version\"" in snapshot.raw_state_json
    assert snapshot.recent_traces
    assert snapshot.recent_discovery_nodes
    assert snapshot.channel_preferences


async def test_reflection_generator_writes_budgeted_reflection_trace(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(
        tmp_path,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.homeostasis.current_phase = Phase.REFLECTIVE
    with patch(
        "sandbox.extensions.soul.reflection_runtime.Runner.run",
        new=AsyncMock(
            return_value=SimpleNamespace(
                final_output="User keeps circling purpose; stay gentle."
            )
        ),
    ) as run_mock:
        await ext._maybe_generate_reflection(datetime.now(UTC))

    metrics = await ext._storage.get_daily_metrics(datetime.now(UTC).date())
    with sqlite3.connect(context.data_dir / "soul.db") as conn:
        rows = conn.execute(
            "SELECT trace_type, content FROM traces WHERE trace_type = 'reflection'"
        ).fetchall()

    assert run_mock.await_count == 1
    assert metrics is not None
    assert metrics["reflection_count"] == 1
    assert rows


class _RuntimeKvStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str | None) -> None:
        if value is None:
            self.values.pop(key, None)
            return
        self.values[key] = value


async def test_internal_exploration_writes_trace_and_respects_novelty(
    tmp_path: Path,
) -> None:
    kv = _RuntimeKvStore()
    context = FakeSoulContext(
        tmp_path,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
        extensions={"kv": kv},
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    assert ext._storage is not None
    ext._state.homeostasis.current_phase = Phase.CURIOUS
    now = datetime.now(UTC)
    for index in range(3):
        await ext._storage.append_trace(
            trace_type="interaction",
            phase=Phase.CURIOUS.value,
            content=f"Trace {index}",
            created_at=now - timedelta(minutes=index + 1),
        )

    with patch(
        "sandbox.extensions.soul.exploration_runtime.Runner.run",
        new=AsyncMock(
            return_value=SimpleNamespace(
                final_output="The user returns to the same unresolved topic."
            )
        ),
    ) as run_mock:
        await ext._maybe_explore_internal_space(now)

    with sqlite3.connect(context.data_dir / "soul.db") as conn:
        rows = conn.execute(
            "SELECT trace_type FROM traces WHERE trace_type = 'exploration'"
        ).fetchall()

    assert run_mock.await_count == 1
    assert rows


async def test_internal_exploration_novelty_exhaustion_lowers_curiosity(
    tmp_path: Path,
) -> None:
    kv = _RuntimeKvStore()
    context = FakeSoulContext(
        tmp_path,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
        extensions={"kv": kv},
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    assert ext._storage is not None
    ext._state.homeostasis.current_phase = Phase.CURIOUS
    ext._state.homeostasis.curiosity = 0.8
    now = datetime.now(UTC)
    for index in range(3):
        await ext._storage.append_trace(
            trace_type="interaction",
            phase=Phase.CURIOUS.value,
            content=f"Trace {index}",
            created_at=now - timedelta(minutes=index + 1),
        )

    with patch(
        "sandbox.extensions.soul.exploration_runtime.Runner.run",
        new=AsyncMock(return_value=SimpleNamespace(final_output="Same observation.")),
    ):
        await ext._maybe_explore_internal_space(now)
        await ext._maybe_explore_internal_space(now + timedelta(minutes=1))
        await ext._maybe_explore_internal_space(now + timedelta(minutes=2))
        await ext._maybe_explore_internal_space(now + timedelta(minutes=3))

    assert ext._state.homeostasis.curiosity < 0.8


async def test_outreach_attempt_records_pending_and_emits_event(tmp_path: Path) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.user_presence.estimated_availability = 0.8
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)

    await ext._send_outreach("I was thinking about one thing...", now=now)

    assert context.notifications == [("I was thinking about one thing...", None)]
    assert ext._state.initiative.pending_outreach is not None
    assert ext._state.initiative.budget.used_today == 1
    assert any(topic == "companion.outreach.attempted" for topic, _ in context.events)


async def test_outreach_uses_preferred_channel_metadata_when_available(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    await ext._on_user_message(
        {
            "text": "hello from telegram",
            "channel": SimpleNamespace(channel_id="telegram_channel"),
        }
    )
    await ext._send_outreach("Ping", now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC))

    assert context.notifications[-1] == ("Ping", "telegram_channel")


async def test_user_message_resolves_pending_outreach_as_response(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.user_presence.estimated_availability = 0.8
    attempted_at = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    await ext._send_outreach("Ping", now=attempted_at)
    ext._last_agent_response_at = attempted_at

    await ext._on_user_message({"text": "hi", "channel": object()})

    assert ext._state.initiative.pending_outreach is None
    assert ext._state.initiative.last_outreach_result is not None
    assert ext._state.initiative.last_outreach_result.value == "response"
    assert any(
        topic == "companion.outreach.result" and payload["result"] == "response"
        for topic, payload in context.events
    )


async def test_tick_resolves_pending_outreach_as_ignored_when_available(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.user_presence.estimated_availability = 0.8
    attempted_at = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    await ext._send_outreach("Ping", now=attempted_at)

    await ext._run_one_tick(now=attempted_at + timedelta(minutes=61))

    assert ext._state.initiative.pending_outreach is None
    assert ext._state.initiative.last_outreach_result is not None
    assert ext._state.initiative.last_outreach_result.value == "ignored"
    assert ext._state.initiative.cooldown_until is not None


async def test_tick_resolves_pending_outreach_as_timing_miss_when_unavailable(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.user_presence.estimated_availability = 0.2
    attempted_at = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    await ext._send_outreach("Ping", now=attempted_at)

    await ext._run_one_tick(now=attempted_at + timedelta(minutes=61))

    assert ext._state.initiative.pending_outreach is None
    assert ext._state.initiative.last_outreach_result is not None
    assert ext._state.initiative.last_outreach_result.value == "timing_miss"
    assert ext._state.initiative.cooldown_until is None


async def test_tick_triggers_one_shot_outreach_when_threshold_and_governor_allow(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.user_presence.estimated_availability = 0.8
    ext._state.homeostasis.social_hunger = 0.9
    ext._state.homeostasis.current_phase = Phase.CURIOUS
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    ext._state.homeostasis.phase_entered_at = now - timedelta(minutes=10)
    ext._state.homeostasis.last_tick_at = now - timedelta(minutes=30)

    await ext._run_one_tick(now=now)

    assert len(context.notifications) == 1
    assert "?" in context.notifications[0][0]
    assert ext._state.initiative.budget.used_today == 1


async def test_tick_does_not_trigger_outreach_when_budget_spent(tmp_path: Path) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.user_presence.estimated_availability = 0.8
    ext._state.homeostasis.social_hunger = 0.9
    ext._state.homeostasis.current_phase = Phase.CURIOUS
    ext._state.initiative.budget.used_today = 5
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    ext._state.homeostasis.phase_entered_at = now - timedelta(minutes=10)
    ext._state.homeostasis.last_tick_at = now - timedelta(minutes=30)

    await ext._run_one_tick(now=now)

    assert context.notifications == []


async def test_user_message_creates_discovery_node_and_updates_topic_coverage(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    assert ext._storage is not None

    await ext._on_user_message(
        {
            "text": "My name is Vitaly and I build agent runtimes.",
            "channel": object(),
        }
    )

    nodes = await ext._storage.list_discovery_nodes(limit=5)

    assert ext._state.discovery.interaction_count == 1
    assert ext._state.discovery.topics.identity > 0.0
    assert nodes
    assert nodes[0]["topic"] in {"identity", "work"}


async def test_discovery_lifecycle_transitions_to_forming_after_enough_interactions(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    for index in range(20):
        await ext._on_user_message(
            {
                "text": f"I work on agent systems and prefer short messages {index}",
                "channel": object(),
            }
        )

    assert ext._state.discovery.lifecycle_phase is SoulLifecyclePhase.FORMING
    assert any(topic == "companion.lifecycle.changed" for topic, _ in context.events)


async def test_discovery_outreach_prefers_question_about_unknown_topic(
    tmp_path: Path,
) -> None:
    context = FakeSoulContext(
        tmp_path,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
    )
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._state is not None
    ext._state.homeostasis.current_phase = Phase.CURIOUS
    ext._state.discovery.topics.identity = 0.8
    ext._state.discovery.topics.work = 0.7
    ext._state.discovery.topics.rhythm = 0.1
    ext._state.discovery.topics.communication = 0.6
    ext._state.discovery.topics.interests = 0.6
    now = datetime.now(UTC)

    with patch(
        "sandbox.extensions.soul.discovery_runtime.Runner.run",
        new=AsyncMock(
            return_value=SimpleNamespace(
                final_output="What does a normal day usually feel like for you right now?"
            )
        ),
    ):
        text = await ext._build_outreach_text(now)

    assert "?" in text
    assert "day" in text.lower()
    assert ext._state.discovery.last_question_topic == "rhythm"
