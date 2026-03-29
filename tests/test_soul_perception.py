from datetime import UTC, datetime, timedelta

from sandbox.extensions.soul.models import PerceptionSignals, PerceptionWindowState
from sandbox.extensions.soul.perception import (
    HeuristicPerceptionInput,
    append_window_sample,
    collapse_window,
    infer_signals,
    smooth_signals,
)


def test_open_message_increases_openness_and_joy() -> None:
    signals = infer_signals(
        HeuristicPerceptionInput(
            text="How did the architecture change after the runtime refactor? 🙂",
            seconds_since_last_user_message=45,
            response_delay_seconds=60,
        )
    )

    assert signals.openness_signal > 0.45
    assert signals.joy_signal > 0.15
    assert signals.withdrawal_signal < signals.openness_signal


def test_brief_delayed_message_increases_withdrawal_and_fatigue() -> None:
    signals = infer_signals(
        HeuristicPerceptionInput(
            text="ok...",
            seconds_since_last_user_message=1800,
            response_delay_seconds=5 * 3600,
        )
    )

    assert signals.withdrawal_signal > 0.45
    assert signals.fatigue_signal > 0.40
    assert signals.openness_signal < signals.withdrawal_signal


def test_smoothing_keeps_signals_probabilistic() -> None:
    previous = PerceptionSignals(openness_signal=0.2, stress_signal=0.1)
    current = PerceptionSignals(openness_signal=0.9, stress_signal=0.8)

    blended = smooth_signals(previous, current, alpha=0.35)

    assert 0.2 < blended.openness_signal < 0.9
    assert 0.1 < blended.stress_signal < 0.8


def test_sliding_window_preserves_recent_context_with_decay() -> None:
    start = datetime(2026, 3, 1, tzinfo=UTC)
    window = PerceptionWindowState()
    window = append_window_sample(
        window,
        PerceptionSignals(openness_signal=0.2),
        observed_at=start,
    )
    window = append_window_sample(
        window,
        PerceptionSignals(openness_signal=0.8),
        observed_at=start + timedelta(days=1),
    )

    collapsed = collapse_window(window, decay=0.7)

    assert 0.2 < collapsed.openness_signal < 0.8


def test_outlier_dampening_prevents_single_message_takeover() -> None:
    start = datetime(2026, 3, 1, tzinfo=UTC)
    window = PerceptionWindowState()
    for index in range(4):
        window = append_window_sample(
            window,
            PerceptionSignals(stress_signal=0.1, openness_signal=0.2),
            observed_at=start + timedelta(minutes=index),
        )

    window = append_window_sample(
        window,
        PerceptionSignals(stress_signal=1.0, openness_signal=0.0),
        observed_at=start + timedelta(minutes=10),
    )
    collapsed = collapse_window(window)

    assert collapsed.stress_signal < 0.7
