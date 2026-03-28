from datetime import date, datetime, timedelta, timezone
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
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    await storage.upsert_daily_metrics(
        date.today(),
        outreach_attempts=1,
        inference_count=2,
    )
    deleted = await storage.cleanup_traces_older_than(
        datetime.now(timezone.utc) - timedelta(days=1)
    )

    assert deleted == 1
