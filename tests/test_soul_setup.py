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


async def test_setup_provider_persists_optional_questionnaire_answers(tmp_path) -> None:
    kv = FakeKvStore()
    context = FakeSoulContext(tmp_path, extensions={"kv": kv})
    ext = SoulExtension()
    await ext.initialize(context)

    schema = ext.get_setup_schema()
    await ext.apply_config("companionship_style", "expressive")
    ok, message = await ext.on_setup_complete()

    assert len(schema) == 3
    assert schema[0]["required"] is False
    assert kv.values["soul.setup.companionship_style"] == "expressive"
    assert ok is True
    assert "seed" in message.lower() or "defaults" in message.lower()


async def test_initialize_applies_questionnaire_seed_on_first_start(tmp_path) -> None:
    kv = FakeKvStore()
    kv.values["soul.setup.companionship_style"] = "expressive"
    kv.values["soul.setup.conversation_depth"] = "deep"
    kv.values["soul.setup.energy_style"] = "playful"
    context = FakeSoulContext(tmp_path, extensions={"kv": kv})
    ext = SoulExtension()

    await ext.initialize(context)

    assert ext._state is not None
    assert ext._state.temperament.seed_source == "questionnaire"
    assert ext._state.temperament.sociability > 0.5
    assert ext._state.temperament.depth > 0.5
    assert ext._state.temperament.playfulness > 0.5
