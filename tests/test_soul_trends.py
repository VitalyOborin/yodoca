from datetime import UTC, datetime, timedelta

from sandbox.extensions.soul.trends import (
    build_daily_summaries,
    compute_relationship_trend,
)


def test_build_daily_summaries_and_trends_are_explainable() -> None:
    start = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    interactions = []
    for day in range(10):
        day_start = start + timedelta(days=day)
        interactions.append(
            {
                "direction": "inbound",
                "created_at": day_start.isoformat(),
                "message_length": 40 + day * 5,
                "openness_signal": 0.2 + (day * 0.05),
                "channel_id": "cli_channel",
                "hour": day_start.hour,
                "day_of_week": day_start.weekday(),
                "outreach_result": None,
                "response_delay_s": None,
            }
        )
        interactions.append(
            {
                "direction": "outbound",
                "created_at": (day_start + timedelta(minutes=5)).isoformat(),
                "message_length": 20,
                "openness_signal": None,
                "channel_id": "cli_channel",
                "hour": day_start.hour,
                "day_of_week": day_start.weekday(),
                "outreach_result": None,
                "response_delay_s": None,
            }
        )

    summaries = build_daily_summaries(interactions)
    trend = compute_relationship_trend(summaries, recent_days=3)

    assert len(summaries) == 10
    assert trend.openness_trend > 0
    assert trend.message_depth_trend > 0
    assert "openness_trend" in trend.explanations()


def test_user_started_ratio_drops_when_agent_starts_recent_conversations() -> None:
    start = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    interactions = []
    for day in range(8):
        day_start = start + timedelta(days=day)
        first_direction = "inbound" if day < 5 else "outbound"
        interactions.append(
            {
                "direction": first_direction,
                "created_at": day_start.isoformat(),
                "message_length": 40,
                "openness_signal": 0.4,
                "channel_id": "cli_channel",
                "hour": day_start.hour,
                "day_of_week": day_start.weekday(),
                "outreach_result": None,
                "response_delay_s": None,
            }
        )

    summaries = build_daily_summaries(interactions)
    trend = compute_relationship_trend(summaries, recent_days=3)

    assert trend.initiative_ratio_trend < 0
