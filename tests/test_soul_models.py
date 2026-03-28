from sandbox.extensions.soul.models import CompanionState, Phase, PresenceState


def test_companion_state_json_round_trip() -> None:
    state = CompanionState()
    state.homeostasis.current_phase = Phase.REFLECTIVE
    state.presence = PresenceState.WARM
    state.mood = 0.25
    state.tick_count = 42
    state.perception.openness_signal = 0.6
    state.temperament.playfulness = 0.7

    restored = CompanionState.from_json(state.to_json())

    assert restored.version == 1
    assert restored.homeostasis.current_phase is Phase.REFLECTIVE
    assert restored.presence is PresenceState.WARM
    assert restored.mood == 0.25
    assert restored.tick_count == 42
    assert restored.perception.openness_signal == 0.6
    assert restored.temperament.playfulness == 0.7
