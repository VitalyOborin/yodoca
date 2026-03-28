import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sandbox.extensions.soul.models import CompanionState, Phase
from sandbox.extensions.soul.storage import SoulStorage


async def test_soul_storage_state_and_metrics_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "soul.db"
    schema_path = Path("sandbox/extensions/soul/schema.sql")
    storage = SoulStorage(db_path, schema_path)
    await storage.initialize()

    state = CompanionState()
    state.homeostasis.current_phase = Phase.CURIOUS
    state.tick_count = 3
    await storage.save_state(state)

    restored = await storage.load_state()

    assert restored is not None
    assert restored.homeostasis.current_phase is Phase.CURIOUS
    assert restored.tick_count == 3

    await storage.append_trace(
        trace_type="phase_transition",
        phase="CURIOUS",
        content="Entered curious phase",
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    await storage.upsert_daily_metrics(
        date.today(),
        outreach_attempts=1,
        message_count=2,
    )
    deleted = await storage.cleanup_traces_older_than(
        datetime.now(UTC) - timedelta(days=1)
    )
    await storage.append_interaction(
        direction="inbound",
        channel_id="cli_channel",
        response_delay_s=42,
    )
    summary = await storage.get_presence_summary(
        hour=datetime.now(UTC).hour,
        day_of_week=datetime.now(UTC).weekday(),
        since=datetime.now(UTC) - timedelta(days=14),
    )

    assert deleted == 1
    assert summary["total_interactions"] >= 1
    assert summary["last_interaction_at"] is not None

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT direction, channel_id, response_delay_s FROM interaction_log"
        ).fetchone()

    assert row == ("inbound", "cli_channel", 42)
