import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from conftest import FakeSoulContext

from sandbox.extensions.soul.main import SoulExtension


async def test_initialize_sets_up_mood_classifier_agent_when_model_router_exists(
    tmp_path,
) -> None:
    context = FakeSoulContext(
        tmp_path,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
    )
    ext = SoulExtension()

    await ext.initialize(context)

    assert ext._classifier.available


async def test_user_message_triggers_llm_classification_with_budget_guard(
    tmp_path,
) -> None:
    context = FakeSoulContext(
        tmp_path,
        config={
            "mood_classifier_daily_budget": 2,
            "mood_classifier_min_chars": 20,
        },
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
    )
    ext = SoulExtension()
    await ext.initialize(context)

    with patch(
        "sandbox.extensions.soul.classifier_runtime.Runner.run",
        new=AsyncMock(
            return_value=SimpleNamespace(
                final_output=(
                    '{"stress_signal": 0.9, "withdrawal_signal": 0.2, '
                    '"openness_signal": 0.3, "fatigue_signal": 0.8, '
                    '"joy_signal": 0.1, "confidence": 0.85}'
                )
            )
        ),
    ) as run_mock:
        await ext._on_user_message(
            {
                "text": "I am exhausted and a bit overwhelmed by everything today.",
                "channel": object(),
            }
        )
        assert ext._classifier.active_tasks
        await asyncio.gather(*ext._classifier.active_tasks)
        await ext.stop()

    assert run_mock.await_count == 1
    assert ext._state is not None
    assert ext._state.perception.stress_signal > 0.2
    assert ext._state.homeostasis.care_impulse > 0.0
    metrics = await ext._storage.get_daily_metrics(ext._state.homeostasis.last_tick_at.date())
    assert metrics is not None
    assert metrics["inference_count"] == 1


async def test_budget_exhaustion_skips_llm_classification(tmp_path) -> None:
    context = FakeSoulContext(
        tmp_path,
        config={
            "mood_classifier_daily_budget": 1,
            "mood_classifier_min_chars": 10,
        },
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
    )
    ext = SoulExtension()
    await ext.initialize(context)
    assert ext._state is not None
    assert ext._storage is not None
    await ext._storage.upsert_daily_metrics(
        ext._state.homeostasis.last_tick_at.date(),
        inference_count=1,
    )

    with patch(
        "sandbox.extensions.soul.classifier_runtime.Runner.run",
        new=AsyncMock(),
    ) as run_mock:
        await ext._on_user_message(
            {
                "text": "This is definitely long enough to qualify.",
                "channel": object(),
            }
        )
        await ext.stop()

    run_mock.assert_not_awaited()
