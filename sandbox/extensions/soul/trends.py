"""Explainable relationship trend model for Stage 3."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import fmean
from typing import Any


class TrendCache:
    """Time-limited cache for computed relationship trends."""

    def __init__(self, *, ttl_seconds: float = 300) -> None:
        self._trend: RelationshipTrend | None = None
        self._refreshed_at: datetime | None = None
        self._ttl_seconds = ttl_seconds

    def get(self, now: datetime) -> RelationshipTrend | None:
        if self._trend is None or self._refreshed_at is None:
            return None
        if (now - self._refreshed_at).total_seconds() > self._ttl_seconds:
            return None
        return self._trend

    def set(self, trend: RelationshipTrend, *, now: datetime) -> None:
        self._trend = trend
        self._refreshed_at = now

    def invalidate(self) -> None:
        self._trend = None
        self._refreshed_at = None


@dataclass(slots=True)
class RelationshipTrend:
    openness_trend: float = 0.0
    message_depth_trend: float = 0.0
    initiative_ratio_trend: float = 0.0

    def explanations(self) -> dict[str, str]:
        return {
            "openness_trend": "Recent user openness compared with the longer baseline.",
            "message_depth_trend": "Recent inbound message depth compared with the longer baseline.",
            "initiative_ratio_trend": "How often the user starts conversations compared with the longer baseline.",
        }

    def context_note(self) -> str | None:
        if self.openness_trend >= 0.12:
            return "User has been opening up more lately; gentle depth is welcome."
        if self.openness_trend <= -0.12:
            return "User has been less open lately; keep questions light."
        if self.initiative_ratio_trend <= -0.18:
            return "The companion has initiated more lately; avoid pushing."
        if self.message_depth_trend <= -18:
            return "Recent messages have been shorter than usual; stay concise."
        if self.message_depth_trend >= 18:
            return "Recent messages have been deeper than usual; reflective follow-through is okay."
        return None


@dataclass(slots=True)
class DailyRelationshipSummary:
    date: str
    avg_openness: float = 0.0
    avg_message_length: float = 0.0
    user_started_ratio: float = 0.0


def build_daily_summaries(
    interactions: list[dict[str, Any]],
    *,
    inactivity_gap: timedelta = timedelta(hours=4),
) -> list[DailyRelationshipSummary]:
    by_date: dict[str, dict[str, list[float] | int]] = defaultdict(
        lambda: {
            "openness": [],
            "message_lengths": [],
            "conversation_starts": 0,
            "user_started": 0,
        }
    )
    previous_at: datetime | None = None

    for row in interactions:
        created_at = datetime.fromisoformat(str(row["created_at"]))
        date_key = created_at.date().isoformat()
        bucket = by_date[date_key]

        if row.get("direction") == "inbound":
            openness = row.get("openness_signal")
            if openness is not None:
                bucket["openness"].append(float(openness))
            message_length = row.get("message_length")
            if message_length is not None:
                bucket["message_lengths"].append(float(message_length))

        is_conversation_start = (
            previous_at is None or created_at - previous_at >= inactivity_gap
        )
        if is_conversation_start:
            bucket["conversation_starts"] += 1
            if row.get("direction") == "inbound":
                bucket["user_started"] += 1

        previous_at = created_at

    summaries: list[DailyRelationshipSummary] = []
    for date_key, bucket in sorted(by_date.items()):
        openness_values = list(bucket["openness"])
        depth_values = list(bucket["message_lengths"])
        starts = int(bucket["conversation_starts"])
        user_started = int(bucket["user_started"])
        summaries.append(
            DailyRelationshipSummary(
                date=date_key,
                avg_openness=fmean(openness_values) if openness_values else 0.0,
                avg_message_length=fmean(depth_values) if depth_values else 0.0,
                user_started_ratio=(user_started / starts) if starts else 0.0,
            )
        )
    return summaries


def compute_relationship_trend(
    daily_summaries: list[DailyRelationshipSummary],
    *,
    recent_days: int = 7,
) -> RelationshipTrend:
    if not daily_summaries:
        return RelationshipTrend()

    recent = daily_summaries[-recent_days:]
    baseline = daily_summaries[:-recent_days] or recent

    def trend_of(attribute: str) -> float:
        recent_values = [float(getattr(item, attribute)) for item in recent]
        baseline_values = [float(getattr(item, attribute)) for item in baseline]
        return round(fmean(recent_values) - fmean(baseline_values), 4)

    return RelationshipTrend(
        openness_trend=trend_of("avg_openness"),
        message_depth_trend=trend_of("avg_message_length"),
        initiative_ratio_trend=trend_of("user_started_ratio"),
    )
