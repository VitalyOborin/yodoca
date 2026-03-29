"""Trigger-based LLM mood classification helpers for Stage 3."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sandbox.extensions.soul.models import PerceptionSignals


@dataclass(slots=True)
class MoodClassification:
    stress_signal: float
    withdrawal_signal: float
    openness_signal: float
    fatigue_signal: float
    joy_signal: float
    confidence: float

    def to_perception(self) -> PerceptionSignals:
        return PerceptionSignals(
            stress_signal=self.stress_signal,
            withdrawal_signal=self.withdrawal_signal,
            openness_signal=self.openness_signal,
            fatigue_signal=self.fatigue_signal,
            joy_signal=self.joy_signal,
        )


def should_trigger_classification(
    *,
    text: str,
    heuristic: PerceptionSignals,
    min_chars: int,
    signal_threshold: float,
) -> bool:
    stripped = text.strip()
    if len(stripped) >= min_chars:
        return True

    strongest_signal = max(
        heuristic.stress_signal,
        heuristic.withdrawal_signal,
        heuristic.openness_signal,
        heuristic.fatigue_signal,
        heuristic.joy_signal,
    )
    return strongest_signal >= signal_threshold


def parse_classification_output(raw: str) -> MoodClassification | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    try:
        return MoodClassification(
            stress_signal=_clamp(payload.get("stress_signal", 0.0)),
            withdrawal_signal=_clamp(payload.get("withdrawal_signal", 0.0)),
            openness_signal=_clamp(payload.get("openness_signal", 0.0)),
            fatigue_signal=_clamp(payload.get("fatigue_signal", 0.0)),
            joy_signal=_clamp(payload.get("joy_signal", 0.0)),
            confidence=_clamp(payload.get("confidence", 0.0)),
        )
    except (TypeError, ValueError):
        return None


def blend_with_heuristics(
    baseline: PerceptionSignals,
    classification: MoodClassification,
    *,
    weight: float,
) -> PerceptionSignals:
    llm_weight = _clamp(weight)
    heuristic_weight = 1.0 - llm_weight
    llm = classification.to_perception()
    return PerceptionSignals(
        stress_signal=_blend(
            baseline.stress_signal, llm.stress_signal, heuristic_weight, llm_weight
        ),
        withdrawal_signal=_blend(
            baseline.withdrawal_signal,
            llm.withdrawal_signal,
            heuristic_weight,
            llm_weight,
        ),
        openness_signal=_blend(
            baseline.openness_signal, llm.openness_signal, heuristic_weight, llm_weight
        ),
        fatigue_signal=_blend(
            baseline.fatigue_signal, llm.fatigue_signal, heuristic_weight, llm_weight
        ),
        joy_signal=_blend(
            baseline.joy_signal, llm.joy_signal, heuristic_weight, llm_weight
        ),
    )


def _blend(
    baseline_value: float,
    llm_value: float,
    heuristic_weight: float,
    llm_weight: float,
) -> float:
    return round(
        (baseline_value * heuristic_weight) + (llm_value * llm_weight),
        4,
    )


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))
