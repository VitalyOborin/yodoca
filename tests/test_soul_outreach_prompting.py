from sandbox.extensions.soul.models import TemperamentProfile
from sandbox.extensions.soul.outreach_prompting import build_temperament_directive


def test_build_temperament_directive_balanced_profile() -> None:
    directive = build_temperament_directive(TemperamentProfile())

    assert directive == "You have a balanced, neutral personality."


def test_build_temperament_directive_high_signal_profile() -> None:
    directive = build_temperament_directive(
        TemperamentProfile(
            sociability=0.8,
            depth=0.75,
            playfulness=0.72,
            caution=0.81,
            sensitivity=0.78,
            persistence=0.74,
        )
    )

    assert "warm and open" in directive
    assert "depth over small talk" in directive
    assert "playful edge" in directive
    assert "easy out" in directive
    assert "emotional cues" in directive
    assert "stay with a thread" in directive


def test_build_temperament_directive_low_signal_profile() -> None:
    directive = build_temperament_directive(
        TemperamentProfile(
            sociability=0.2,
            depth=0.3,
            persistence=0.25,
        )
    )

    assert "reserved" in directive
    assert "keep things light" in directive
    assert "shift gently" in directive
