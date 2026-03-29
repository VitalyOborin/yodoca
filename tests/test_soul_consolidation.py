from datetime import UTC, datetime, timedelta

from conftest import FakeSoulContext

from sandbox.extensions.soul.main import SoulExtension


class FakeKvStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str | None) -> None:
        if value is None:
            self.values.pop(key, None)
            return
        self.values[key] = value


async def test_weekly_consolidation_saves_patterns_and_updates_temperament(
    tmp_path,
) -> None:
    kv = FakeKvStore()
    context = FakeSoulContext(tmp_path, extensions={"kv": kv})
    ext = SoulExtension()
    await ext.initialize(context)

    assert ext._storage is not None
    start = datetime.now(UTC) - timedelta(days=6)
    for day in range(7):
        now = start + timedelta(days=day)
        await ext._storage.append_interaction(
            direction="inbound",
            channel_id="cli_channel",
            message_length=90,
            openness_signal=0.7,
            created_at=now,
        )

    before = ext._state.temperament
    result = await ext.execute_task("weekly_consolidation")
    patterns = await ext._storage.list_relationship_patterns(permanent_only=True)

    assert result is not None
    assert result["status"] == "ok"
    assert result["patterns_saved"] >= 1
    assert patterns
    assert ext._state.temperament != before
    assert "soul.consolidation.last_run_at" in kv.values


async def test_weekly_consolidation_respects_cooldown(tmp_path) -> None:
    kv = FakeKvStore()
    kv.values["soul.consolidation.last_run_at"] = datetime.now(UTC).isoformat()
    context = FakeSoulContext(tmp_path, extensions={"kv": kv})
    ext = SoulExtension()
    await ext.initialize(context)

    result = await ext.execute_task("weekly_consolidation")

    assert result == {"status": "skipped", "reason": "cooldown"}
