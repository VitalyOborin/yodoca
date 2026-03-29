from datetime import UTC, datetime

from sandbox.extensions.soul.models import (
    CompanionState,
    OutreachResult,
    PerceptionSample,
    Phase,
    PresenceState,
    SoulLifecyclePhase,
)


def test_companion_state_json_round_trip() -> None:
    state = CompanionState()
    state.homeostasis.current_phase = Phase.REFLECTIVE
    state.presence = PresenceState.WARM
    state.mood = 0.25
    state.tick_count = 42
    state.perception.openness_signal = 0.6
    state.perception_window.samples.append(
        PerceptionSample(
            observed_at=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
            signals=state.perception,
        )
    )
    state.user_presence.estimated_availability = 0.55
    state.initiative.budget.used_today = 1
    state.initiative.last_outreach_result = OutreachResult.RESPONSE
    state.temperament.playfulness = 0.7
    state.temperament.drift_events = 2
    state.temperament.seed_source = "questionnaire"
    state.discovery.lifecycle_phase = SoulLifecyclePhase.FORMING
    state.discovery.interaction_count = 17
    state.discovery.topics.work = 0.8

    restored = CompanionState.from_json(state.to_json())

    assert restored.version == 1
    assert restored.homeostasis.current_phase is Phase.REFLECTIVE
    assert restored.presence is PresenceState.WARM
    assert restored.mood == 0.25
    assert restored.tick_count == 42
    assert restored.perception.openness_signal == 0.6
    assert len(restored.perception_window.samples) == 1
    assert restored.user_presence.estimated_availability == 0.55
    assert restored.initiative.budget.used_today == 1
    assert restored.initiative.last_outreach_result is OutreachResult.RESPONSE
    assert restored.temperament.playfulness == 0.7
    assert restored.temperament.drift_events == 2
    assert restored.temperament.seed_source == "questionnaire"
    assert restored.discovery.lifecycle_phase is SoulLifecyclePhase.FORMING
    assert restored.discovery.interaction_count == 17
    assert restored.discovery.topics.work == 0.8
