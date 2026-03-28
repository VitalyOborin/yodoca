from datetime import UTC, datetime, timedelta

from sandbox.extensions.soul.presence import DEFAULT_AVAILABILITY, estimate_availability


def test_recent_interaction_means_high_availability() -> None:
    now = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)

    score = estimate_availability(
        now=now,
        last_interaction_at=now - timedelta(minutes=10),
        slot_interactions=0,
        total_interactions=0,
    )

    assert score == 0.95


def test_low_data_defaults_to_conservative_availability() -> None:
    score = estimate_availability(
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
        last_interaction_at=None,
        slot_interactions=1,
        total_interactions=2,
    )

    assert score == DEFAULT_AVAILABILITY


def test_slot_activity_raises_availability_but_stays_bounded() -> None:
    score = estimate_availability(
        now=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
        last_interaction_at=None,
        slot_interactions=8,
        total_interactions=10,
    )

    assert 0.3 < score <= 0.85
