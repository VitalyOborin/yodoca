"""Heuristic perception signals for the soul runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sandbox.extensions.soul.models import (
    PerceptionSample,
    PerceptionSignals,
    PerceptionWindowState,
)

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "]",
)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(slots=True)
class HeuristicPerceptionInput:
    text: str
    seconds_since_last_user_message: float | None = None
    response_delay_seconds: float | None = None


def infer_signals(observation: HeuristicPerceptionInput) -> PerceptionSignals:
    text = observation.text.strip()
    length = len(text)
    question_marks = text.count("?")
    exclamations = text.count("!")
    ellipsis = text.count("...")
    emoji_count = len(_EMOJI_RE.findall(text))
    uppercase_ratio = _uppercase_ratio(text)

    brevity = clamp01((24 - min(length, 24)) / 24) if length else 1.0
    richness = clamp01((length - 24) / 120) if length > 24 else 0.0
    question_signal = clamp01(question_marks / 2)
    emoji_signal = clamp01(emoji_count / 2)
    quick_follow_up = _quick_follow_up_score(observation.seconds_since_last_user_message)
    delayed_response = _delayed_response_score(observation.response_delay_seconds)
    punctuation_intensity = clamp01((question_marks + exclamations + ellipsis) / 4)

    withdrawal = clamp01(
        (brevity * 0.50)
        + (delayed_response * 0.20)
        + ((1.0 - question_signal) * 0.10)
        + ((1.0 - emoji_signal) * 0.05)
    )
    openness = clamp01(
        (richness * 0.35)
        + (question_signal * 0.25)
        + (quick_follow_up * 0.20)
        + (emoji_signal * 0.10)
        + ((1.0 - delayed_response) * 0.10)
    )
    fatigue = clamp01(
        (delayed_response * 0.45)
        + (brevity * 0.20)
        + (ellipsis * 0.15)
        + ((1.0 - emoji_signal) * 0.05)
    )
    joy = clamp01(
        (emoji_signal * 0.45)
        + (clamp01(exclamations / 3) * 0.20)
        + (question_signal * 0.10)
        + (quick_follow_up * 0.10)
        + (richness * 0.05)
    )
    stress = clamp01(
        (punctuation_intensity * 0.25)
        + (uppercase_ratio * 0.25)
        + (brevity * 0.15)
        + (delayed_response * 0.10)
        + (quick_follow_up * 0.10)
    )

    return PerceptionSignals(
        stress_signal=stress,
        withdrawal_signal=withdrawal,
        openness_signal=openness,
        fatigue_signal=fatigue,
        joy_signal=joy,
    )


def smooth_signals(
    previous: PerceptionSignals,
    current: PerceptionSignals,
    *,
    alpha: float = 0.35,
) -> PerceptionSignals:
    alpha = clamp01(alpha)

    def blend(old: float, new: float) -> float:
        return clamp01((old * (1.0 - alpha)) + (new * alpha))

    return PerceptionSignals(
        stress_signal=blend(previous.stress_signal, current.stress_signal),
        withdrawal_signal=blend(
            previous.withdrawal_signal,
            current.withdrawal_signal,
        ),
        openness_signal=blend(previous.openness_signal, current.openness_signal),
        fatigue_signal=blend(previous.fatigue_signal, current.fatigue_signal),
        joy_signal=blend(previous.joy_signal, current.joy_signal),
    )


def append_window_sample(
    window: PerceptionWindowState,
    current: PerceptionSignals,
    *,
    observed_at: datetime,
    max_samples: int = 8,
    outlier_delta: float = 0.45,
    outlier_dampening: float = 0.6,
) -> PerceptionWindowState:
    baseline = collapse_window(window)
    sample = PerceptionSample(
        observed_at=observed_at,
        signals=_dampen_outlier(
            current,
            baseline=baseline,
            outlier_delta=outlier_delta,
            dampening=outlier_dampening,
        ),
    )
    samples = [*window.samples, sample][-max_samples:]
    return PerceptionWindowState(samples=samples)


def collapse_window(
    window: PerceptionWindowState,
    *,
    decay: float = 0.72,
) -> PerceptionSignals:
    if not window.samples:
        return PerceptionSignals()

    decay = clamp01(decay)
    weights: list[float] = []
    for index in range(len(window.samples)):
        age = (len(window.samples) - 1) - index
        weights.append(decay**age)

    def average(selector: str) -> float:
        numerator = 0.0
        denominator = 0.0
        for sample, weight in zip(window.samples, weights, strict=False):
            numerator += getattr(sample.signals, selector) * weight
            denominator += weight
        if denominator == 0.0:
            return 0.0
        return round(clamp01(numerator / denominator), 4)

    return PerceptionSignals(
        stress_signal=average("stress_signal"),
        withdrawal_signal=average("withdrawal_signal"),
        openness_signal=average("openness_signal"),
        fatigue_signal=average("fatigue_signal"),
        joy_signal=average("joy_signal"),
    )


def _dampen_outlier(
    current: PerceptionSignals,
    *,
    baseline: PerceptionSignals,
    outlier_delta: float,
    dampening: float,
) -> PerceptionSignals:
    dampening = clamp01(dampening)

    def adjust(value: float, baseline_value: float) -> float:
        delta = value - baseline_value
        if abs(delta) <= outlier_delta:
            return value
        limited_delta = outlier_delta + ((abs(delta) - outlier_delta) * dampening)
        signed_delta = limited_delta if delta >= 0 else -limited_delta
        return clamp01(baseline_value + signed_delta)

    return PerceptionSignals(
        stress_signal=adjust(current.stress_signal, baseline.stress_signal),
        withdrawal_signal=adjust(
            current.withdrawal_signal,
            baseline.withdrawal_signal,
        ),
        openness_signal=adjust(current.openness_signal, baseline.openness_signal),
        fatigue_signal=adjust(current.fatigue_signal, baseline.fatigue_signal),
        joy_signal=adjust(current.joy_signal, baseline.joy_signal),
    )


def _quick_follow_up_score(seconds_since_last_user_message: float | None) -> float:
    if seconds_since_last_user_message is None:
        return 0.0
    if seconds_since_last_user_message <= 90:
        return 1.0
    if seconds_since_last_user_message <= 300:
        return 0.6
    if seconds_since_last_user_message <= 900:
        return 0.2
    return 0.0


def _delayed_response_score(response_delay_seconds: float | None) -> float:
    if response_delay_seconds is None:
        return 0.0
    if response_delay_seconds >= 8 * 3600:
        return 1.0
    if response_delay_seconds >= 2 * 3600:
        return 0.7
    if response_delay_seconds >= 30 * 60:
        return 0.4
    if response_delay_seconds >= 5 * 60:
        return 0.2
    return 0.0


def _uppercase_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    uppercase = sum(1 for char in letters if char.isupper())
    return clamp01(uppercase / len(letters))
