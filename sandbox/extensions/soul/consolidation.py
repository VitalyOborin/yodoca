"""Weekly consolidation helpers for Stage 4."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime

from sandbox.extensions.soul.models import TemperamentProfile
from sandbox.extensions.soul.temperament import apply_drift
from sandbox.extensions.soul.trends import DailyRelationshipSummary, RelationshipTrend


@dataclass(slots=True)
class RelationshipPattern:
    pattern_key: str
    pattern_type: str
    content: str
    repetition_count: int
    confidence: float
    is_permanent: bool
    source_json: str | None = None


def detect_relationship_patterns(
    summaries: list[DailyRelationshipSummary],
    trend: RelationshipTrend,
) -> list[RelationshipPattern]:
    patterns: list[RelationshipPattern] = []
    if len(summaries) >= 3:
        open_days = sum(1 for item in summaries[-7:] if item.avg_openness >= 0.45)
        if open_days >= 3:
            patterns.append(
                RelationshipPattern(
                    pattern_key="openness_recurring",
                    pattern_type="relationship",
                    content="User has shown sustained openness across recent conversations.",
                    repetition_count=open_days,
                    confidence=round(min(1.0, 0.45 + (open_days * 0.1)), 4),
                    is_permanent=open_days >= 3,
                    source_json=json.dumps(
                        {"open_days": open_days},
                        ensure_ascii=False,
                    ),
                )
            )

        initiator_days = sum(1 for item in summaries[-7:] if item.user_started_ratio >= 1.0)
        if initiator_days >= 3:
            patterns.append(
                RelationshipPattern(
                    pattern_key="user_initiates_recurring",
                    pattern_type="relationship",
                    content="User often starts conversations without prompting.",
                    repetition_count=initiator_days,
                    confidence=round(min(1.0, 0.4 + (initiator_days * 0.1)), 4),
                    is_permanent=initiator_days >= 3,
                    source_json=json.dumps(
                        {"initiator_days": initiator_days},
                        ensure_ascii=False,
                    ),
                )
            )

        deep_days = sum(
            1 for item in summaries[-7:] if item.avg_message_length >= 80
        )
        if deep_days >= 3:
            patterns.append(
                RelationshipPattern(
                    pattern_key="deep_conversation_recurring",
                    pattern_type="relationship",
                    content="Recent conversations repeatedly trend toward deeper exchanges.",
                    repetition_count=deep_days,
                    confidence=round(min(1.0, 0.4 + (deep_days * 0.1)), 4),
                    is_permanent=deep_days >= 3,
                    source_json=json.dumps(
                        {"deep_days": deep_days},
                        ensure_ascii=False,
                    ),
                )
            )

    if trend.openness_trend >= 0.12:
        patterns.append(
            RelationshipPattern(
                pattern_key="openness_rising",
                pattern_type="trend",
                content="User openness is rising over the recent baseline.",
                repetition_count=max(3, len(summaries[-7:])),
                confidence=round(min(1.0, 0.5 + trend.openness_trend), 4),
                is_permanent=True,
                source_json=json.dumps(asdict(trend), ensure_ascii=False),
            )
        )
    return patterns


def temperament_targets_from_patterns(
    patterns: list[RelationshipPattern],
    trend: RelationshipTrend,
) -> dict[str, float]:
    targets: dict[str, float] = {}
    if any(item.pattern_key == "user_initiates_recurring" for item in patterns):
        targets["sociability"] = 0.82
        targets["persistence"] = 0.8
        targets["caution"] = 0.18
    if any(item.pattern_key == "deep_conversation_recurring" for item in patterns):
        targets["depth"] = 0.9
        targets["sensitivity"] = 0.86
        targets["playfulness"] = 0.14
    if trend.openness_trend >= 0.12:
        targets["depth"] = max(targets.get("depth", 0.5), 0.92)
        targets["sociability"] = max(targets.get("sociability", 0.5), 0.78)
    if trend.initiative_ratio_trend <= -0.18:
        targets["caution"] = 0.65
    return targets


def apply_identity_shift(
    profile: TemperamentProfile,
    *,
    patterns: list[RelationshipPattern],
    trend: RelationshipTrend,
    seed_source: str = "usage",
) -> TemperamentProfile:
    targets = temperament_targets_from_patterns(patterns, trend)
    if not targets:
        return profile
    return apply_drift(profile, targets=targets, seed_source=seed_source)


def should_run_weekly_consolidation(
    *,
    last_run_at: datetime | None,
    now: datetime,
) -> bool:
    if last_run_at is None:
        return True
    return (now - last_run_at).days >= 7
