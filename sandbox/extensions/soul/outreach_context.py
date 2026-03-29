"""Deterministic context assembly for LLM-native outreach."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sandbox.extensions.soul.models import (
    CompanionState,
    DiscoveryTopicCoverage,
    OutreachResult,
    Phase,
    SoulLifecyclePhase,
    TemperamentProfile,
)

_DISCOVERY_GAP_THRESHOLD = 0.30


@dataclass(frozen=True, slots=True)
class InteractionSummary:
    id: int | None
    direction: str
    channel_id: str | None
    outreach_result: str | None
    message_length: int | None
    openness_signal: float | None
    response_delay_s: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TraceSummary:
    id: int | None
    trace_type: str
    phase: str
    content: str
    payload_json: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DiscoveryNodeSummary:
    id: int | None
    topic: str
    content: str
    confidence: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RelationshipPatternSummary:
    pattern_key: str
    pattern_type: str
    content: str
    repetition_count: int
    confidence: float
    is_permanent: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OutreachContext:
    phase: Phase
    lifecycle: SoulLifecyclePhase
    mood: float
    temperament: TemperamentProfile
    recent_interactions: list[InteractionSummary]
    unfollowed_interactions: list[InteractionSummary]
    recent_traces: list[TraceSummary]
    discovery_nodes: list[DiscoveryNodeSummary]
    relationship_patterns: list[RelationshipPatternSummary]
    discovery_topics: DiscoveryTopicCoverage
    discovery_gaps: list[str]
    relationship_depth: str
    last_outreach_result: OutreachResult | None
    hours_since_last_user_message: float | None
    estimated_availability: float
    daily_metrics: dict[str, Any] | None


async def assemble_outreach_context(
    state: CompanionState,
    storage: Any,
    *,
    now: datetime | None = None,
) -> OutreachContext:
    current_time = now or datetime.now(UTC)
    metric_date = date(
        current_time.astimezone(UTC).year,
        current_time.astimezone(UTC).month,
        current_time.astimezone(UTC).day,
    )
    recent_interactions_raw = await storage.list_recent_interactions(limit=10)
    unfollowed_raw = await storage.list_unfollowed_interactions(limit=5)
    recent_traces_raw = await storage.list_traces_since(
        current_time - timedelta(hours=24),
        trace_types=("reflection", "exploration"),
        limit=8,
    )
    discovery_nodes_raw = await storage.list_discovery_nodes(limit=8)
    relationship_patterns_raw = await storage.list_relationship_patterns(
        permanent_only=False,
    )
    daily_metrics = await storage.get_daily_metrics(metric_date)

    recent_interactions = [
        _interaction_from_row(row) for row in recent_interactions_raw
    ]
    unfollowed_interactions = [_interaction_from_row(row) for row in unfollowed_raw]
    recent_traces = [_trace_from_row(row) for row in recent_traces_raw]
    discovery_nodes = [_discovery_node_from_row(row) for row in discovery_nodes_raw]
    relationship_patterns = [
        _relationship_pattern_from_row(row) for row in relationship_patterns_raw
    ]

    return OutreachContext(
        phase=state.homeostasis.current_phase,
        lifecycle=state.discovery.lifecycle_phase,
        mood=state.mood,
        temperament=state.temperament,
        recent_interactions=recent_interactions,
        unfollowed_interactions=unfollowed_interactions,
        recent_traces=recent_traces,
        discovery_nodes=discovery_nodes,
        relationship_patterns=relationship_patterns,
        discovery_topics=state.discovery.topics,
        discovery_gaps=_compute_discovery_gaps(state.discovery.topics),
        relationship_depth=_compute_relationship_depth(
            state=state,
            relationship_patterns=relationship_patterns,
        ),
        last_outreach_result=state.initiative.last_outreach_result,
        hours_since_last_user_message=_hours_since_last_user_message(
            recent_interactions,
            fallback_at=state.user_presence.last_interaction_at,
            now=current_time,
        ),
        estimated_availability=state.user_presence.estimated_availability,
        daily_metrics=daily_metrics,
    )


def _interaction_from_row(row: dict[str, Any]) -> InteractionSummary:
    return InteractionSummary(
        id=int(row["id"]) if row.get("id") is not None else None,
        direction=str(row["direction"]),
        channel_id=row.get("channel_id"),
        outreach_result=row.get("outreach_result"),
        message_length=_maybe_int(row.get("message_length")),
        openness_signal=_maybe_float(row.get("openness_signal")),
        response_delay_s=_maybe_int(row.get("response_delay_s")),
        created_at=_parse_dt(row["created_at"]),
    )


def _trace_from_row(row: dict[str, Any]) -> TraceSummary:
    return TraceSummary(
        id=int(row["id"]) if row.get("id") is not None else None,
        trace_type=str(row["trace_type"]),
        phase=str(row["phase"]),
        content=str(row["content"]),
        payload_json=row.get("payload_json"),
        created_at=_parse_dt(row["created_at"]),
    )


def _discovery_node_from_row(row: dict[str, Any]) -> DiscoveryNodeSummary:
    return DiscoveryNodeSummary(
        id=int(row["id"]) if row.get("id") is not None else None,
        topic=str(row["topic"]),
        content=str(row["content"]),
        confidence=float(row.get("confidence", 0.0)),
        created_at=_parse_dt(row["created_at"]),
    )


def _relationship_pattern_from_row(
    row: dict[str, Any],
) -> RelationshipPatternSummary:
    return RelationshipPatternSummary(
        pattern_key=str(row["pattern_key"]),
        pattern_type=str(row["pattern_type"]),
        content=str(row["content"]),
        repetition_count=int(row.get("repetition_count", 0)),
        confidence=float(row.get("confidence", 0.0)),
        is_permanent=bool(row.get("is_permanent", False)),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _compute_discovery_gaps(topics: DiscoveryTopicCoverage) -> list[str]:
    values = topics.to_dict()
    return [
        topic
        for topic, coverage in values.items()
        if coverage < _DISCOVERY_GAP_THRESHOLD
    ]


def _compute_relationship_depth(
    *,
    state: CompanionState,
    relationship_patterns: list[RelationshipPatternSummary],
) -> str:
    permanent_count = sum(1 for pattern in relationship_patterns if pattern.is_permanent)
    if state.discovery.lifecycle_phase is SoulLifecyclePhase.MATURE:
        return "established"
    if permanent_count >= 2:
        return "established"
    if state.discovery.lifecycle_phase is SoulLifecyclePhase.FORMING:
        return "forming"
    if relationship_patterns or state.discovery.interaction_count >= 8:
        return "forming"
    return "new"


def _hours_since_last_user_message(
    interactions: list[InteractionSummary],
    *,
    fallback_at: datetime | None,
    now: datetime,
) -> float | None:
    for interaction in interactions:
        if interaction.direction == "inbound":
            return round(
                (now.astimezone(UTC) - interaction.created_at.astimezone(UTC))
                .total_seconds()
                / 3600.0,
                2,
            )
    if fallback_at is None:
        return None
    return round(
        (now.astimezone(UTC) - fallback_at.astimezone(UTC)).total_seconds() / 3600.0,
        2,
    )


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _maybe_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _maybe_float(value: Any) -> float | None:
    return float(value) if value is not None else None
