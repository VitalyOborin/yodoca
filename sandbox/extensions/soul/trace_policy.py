"""Thresholded trace policy — pure detection functions for meaningful events."""

from __future__ import annotations

from typing import Any

from sandbox.extensions.soul.models import CompanionState

_DRIVE_NAMES = (
    "curiosity",
    "social_hunger",
    "rest_need",
    "reflection_need",
    "care_impulse",
    "overstimulation",
)


def detect_perception_shift(
    current: CompanionState,
    previous: CompanionState,
    *,
    threshold: float = 0.2,
) -> dict[str, Any] | None:
    deltas = {
        "stress": abs(
            current.perception.stress_signal - previous.perception.stress_signal
        ),
        "withdrawal": abs(
            current.perception.withdrawal_signal - previous.perception.withdrawal_signal
        ),
        "openness": abs(
            current.perception.openness_signal - previous.perception.openness_signal
        ),
        "fatigue": abs(
            current.perception.fatigue_signal - previous.perception.fatigue_signal
        ),
        "joy": abs(current.perception.joy_signal - previous.perception.joy_signal),
    }
    strongest = max(deltas, key=deltas.get)  # type: ignore[arg-type]
    if deltas[strongest] <= threshold:
        return None
    return {
        "trace_type": "perception_shift",
        "content": f"Perception shifted on {strongest}.",
        "payload": {"deltas": deltas},
    }


def detect_drive_boundary_crossings(
    current: CompanionState,
    previous: CompanionState,
) -> list[dict[str, Any]]:
    crossings: list[dict[str, Any]] = []
    for name in _DRIVE_NAMES:
        before = getattr(previous.homeostasis, name)
        after = getattr(current.homeostasis, name)
        crossed_low = before >= 0.1 and after < 0.1
        crossed_high = before <= 0.9 and after > 0.9
        if not crossed_low and not crossed_high:
            continue
        crossings.append(
            {
                "trace_type": "drive_boundary",
                "content": f"Drive {name} crossed {'high' if crossed_high else 'low'} boundary.",
                "payload": {"drive": name, "before": before, "after": after},
            }
        )
    return crossings
