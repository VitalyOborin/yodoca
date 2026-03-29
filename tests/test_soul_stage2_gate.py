from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import FakeSoulContext

from sandbox.extensions.soul.main import SoulExtension
from sandbox.extensions.soul.models import Phase


async def test_stage2_automated_gate_scenarios(tmp_path: Path) -> None:
    context = FakeSoulContext(tmp_path)
    ext = SoulExtension()
    await ext.initialize(context)
    await ext.start()

    assert ext._state is not None

    noon = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    ext._state.user_presence.estimated_availability = 0.8
    ext._state.homeostasis.current_phase = Phase.CURIOUS
    ext._state.homeostasis.social_hunger = 0.9
    ext._state.homeostasis.last_tick_at = noon - timedelta(hours=4)

    await ext._run_one_tick(now=noon)

    assert len(context.notifications) == 1
    assert ext._state.initiative.pending_outreach is not None

    await ext._on_user_message({"text": "hi", "channel": object()})

    assert ext._state.initiative.last_outreach_result is not None
    assert ext._state.initiative.last_outreach_result.value == "response"
    social_after_response = ext._state.homeostasis.social_hunger

    ext._state.homeostasis.social_hunger = 0.95
    ext._state.initiative.budget.used_today = ext._state.initiative.budget.daily_budget
    await ext._run_one_tick(now=noon + timedelta(hours=1))
    assert len(context.notifications) == 1
    assert ext._state.homeostasis.social_hunger >= social_after_response

    next_day = datetime(2026, 3, 30, 12, 0, tzinfo=UTC)
    ext._state.initiative.budget.last_reset_at = noon
    ext._state.initiative.budget.used_today = 0
    ext._state.user_presence.estimated_availability = 0.8
    ext._state.homeostasis.social_hunger = 0.95
    ext._state.homeostasis.last_tick_at = next_day - timedelta(hours=4)

    await ext._run_one_tick(now=next_day)
    assert len(context.notifications) == 2

    await ext._run_one_tick(now=next_day + timedelta(minutes=61))
    assert ext._state.initiative.last_outreach_result is not None
    assert ext._state.initiative.last_outreach_result.value == "ignored"
    assert ext._state.initiative.cooldown_until is not None

    ext._state.initiative.budget.used_today = 0
    ext._state.initiative.cooldown_until = None
    ext._state.user_presence.estimated_availability = 0.8
    ext._state.homeostasis.social_hunger = 0.95
    night = datetime(2026, 3, 30, 23, 0, tzinfo=UTC)
    ext._state.homeostasis.last_tick_at = night - timedelta(hours=4)

    await ext._run_one_tick(now=night)
    assert len(context.notifications) == 2

    snapshot = await ext._build_state_snapshot()
    assert "daily_budget" in snapshot.initiative
    assert "last_outreach_result" in snapshot.initiative
