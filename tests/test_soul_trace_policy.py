from sandbox.extensions.soul.models import Phase
from sandbox.extensions.soul.trace_policy import detect_phase_transition


def test_detect_phase_transition_returns_trace_payload() -> None:
    transition = detect_phase_transition(
        previous_phase=Phase.AMBIENT,
        current_phase=Phase.CURIOUS,
    )

    assert transition is not None
    assert transition["trace_type"] == "phase_transition"
    assert transition["payload"] == {
        "from_phase": "AMBIENT",
        "to_phase": "CURIOUS",
    }


def test_detect_phase_transition_returns_none_when_phase_is_unchanged() -> None:
    assert (
        detect_phase_transition(
            previous_phase=Phase.AMBIENT,
            current_phase=Phase.AMBIENT,
        )
        is None
    )
