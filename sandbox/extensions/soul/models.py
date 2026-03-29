"""Core state models for the soul companion runtime.

Stage 0 intentionally keeps these models independent from extension lifecycle
code so they can be reused by the simulator and later by the real extension.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _deserialize_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Phase(StrEnum):
    AMBIENT = "AMBIENT"
    CURIOUS = "CURIOUS"
    SOCIAL = "SOCIAL"
    REFLECTIVE = "REFLECTIVE"
    RESTING = "RESTING"
    CARE = "CARE"


class PresenceState(StrEnum):
    SILENT = "SILENT"
    AMBIENT = "AMBIENT"
    ATTENTIVE = "ATTENTIVE"
    WARM = "WARM"
    WITHDRAWN = "WITHDRAWN"
    PLAYFUL = "PLAYFUL"
    REFLECTIVE = "REFLECTIVE"


class OutreachResult(StrEnum):
    RESPONSE = "response"
    IGNORED = "ignored"
    TIMING_MISS = "timing_miss"
    REJECTED = "rejected"


@dataclass(slots=True)
class UserPresenceState:
    last_interaction_at: datetime | None = None
    estimated_availability: float = 0.3

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_interaction_at": (
                _serialize_datetime(self.last_interaction_at)
                if self.last_interaction_at is not None
                else None
            ),
            "estimated_availability": self.estimated_availability,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserPresenceState:
        return cls(
            last_interaction_at=(
                _deserialize_datetime(data["last_interaction_at"])
                if data.get("last_interaction_at")
                else None
            ),
            estimated_availability=float(data.get("estimated_availability", 0.3)),
        )


@dataclass(slots=True)
class PerceptionSignals:
    stress_signal: float = 0.0
    withdrawal_signal: float = 0.0
    openness_signal: float = 0.0
    fatigue_signal: float = 0.0
    joy_signal: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "stress_signal": self.stress_signal,
            "withdrawal_signal": self.withdrawal_signal,
            "openness_signal": self.openness_signal,
            "fatigue_signal": self.fatigue_signal,
            "joy_signal": self.joy_signal,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerceptionSignals:
        return cls(
            stress_signal=float(data.get("stress_signal", 0.0)),
            withdrawal_signal=float(data.get("withdrawal_signal", 0.0)),
            openness_signal=float(data.get("openness_signal", 0.0)),
            fatigue_signal=float(data.get("fatigue_signal", 0.0)),
            joy_signal=float(data.get("joy_signal", 0.0)),
        )


@dataclass(slots=True)
class PerceptionSample:
    observed_at: datetime
    signals: PerceptionSignals

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at": _serialize_datetime(self.observed_at),
            "signals": self.signals.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerceptionSample:
        return cls(
            observed_at=_deserialize_datetime(data["observed_at"]),
            signals=PerceptionSignals.from_dict(data.get("signals", {})),
        )


@dataclass(slots=True)
class PerceptionWindowState:
    samples: list[PerceptionSample] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": [sample.to_dict() for sample in self.samples],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerceptionWindowState:
        raw_samples = data.get("samples", [])
        return cls(
            samples=[
                PerceptionSample.from_dict(sample)
                for sample in raw_samples
                if isinstance(sample, dict)
            ]
        )


@dataclass(slots=True)
class TemperamentProfile:
    sociability: float = 0.5
    depth: float = 0.5
    playfulness: float = 0.5
    caution: float = 0.5
    sensitivity: float = 0.5
    persistence: float = 0.5


@dataclass(slots=True)
class PendingOutreach:
    outreach_id: str
    channel_id: str | None
    attempted_at: datetime
    availability_at_send: float
    window_deadline_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "outreach_id": self.outreach_id,
            "channel_id": self.channel_id,
            "attempted_at": _serialize_datetime(self.attempted_at),
            "availability_at_send": self.availability_at_send,
            "window_deadline_at": _serialize_datetime(self.window_deadline_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingOutreach:
        return cls(
            outreach_id=str(data["outreach_id"]),
            channel_id=data.get("channel_id"),
            attempted_at=_deserialize_datetime(data["attempted_at"]),
            availability_at_send=float(data["availability_at_send"]),
            window_deadline_at=_deserialize_datetime(data["window_deadline_at"]),
        )


@dataclass(slots=True)
class InitiativeBudget:
    daily_budget: int = 1
    used_today: int = 0
    last_reset_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_budget": self.daily_budget,
            "used_today": self.used_today,
            "last_reset_at": _serialize_datetime(self.last_reset_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InitiativeBudget:
        return cls(
            daily_budget=int(data.get("daily_budget", 1)),
            used_today=int(data.get("used_today", 0)),
            last_reset_at=_deserialize_datetime(data["last_reset_at"])
            if data.get("last_reset_at")
            else utc_now(),
        )


@dataclass(slots=True)
class InitiativeState:
    budget: InitiativeBudget = field(default_factory=InitiativeBudget)
    pending_outreach: PendingOutreach | None = None
    cooldown_until: datetime | None = None
    adaptive_threshold: float = 0.75
    last_outreach_at: datetime | None = None
    last_outreach_result: OutreachResult | None = None
    last_result_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget.to_dict(),
            "pending_outreach": (
                self.pending_outreach.to_dict() if self.pending_outreach else None
            ),
            "cooldown_until": (
                _serialize_datetime(self.cooldown_until)
                if self.cooldown_until is not None
                else None
            ),
            "adaptive_threshold": self.adaptive_threshold,
            "last_outreach_at": (
                _serialize_datetime(self.last_outreach_at)
                if self.last_outreach_at is not None
                else None
            ),
            "last_outreach_result": (
                self.last_outreach_result.value
                if self.last_outreach_result is not None
                else None
            ),
            "last_result_at": (
                _serialize_datetime(self.last_result_at)
                if self.last_result_at is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InitiativeState:
        return cls(
            budget=InitiativeBudget.from_dict(data.get("budget", {})),
            pending_outreach=(
                PendingOutreach.from_dict(data["pending_outreach"])
                if data.get("pending_outreach")
                else None
            ),
            cooldown_until=(
                _deserialize_datetime(data["cooldown_until"])
                if data.get("cooldown_until")
                else None
            ),
            adaptive_threshold=float(data.get("adaptive_threshold", 0.75)),
            last_outreach_at=(
                _deserialize_datetime(data["last_outreach_at"])
                if data.get("last_outreach_at")
                else None
            ),
            last_outreach_result=(
                OutreachResult(data["last_outreach_result"])
                if data.get("last_outreach_result")
                else None
            ),
            last_result_at=(
                _deserialize_datetime(data["last_result_at"])
                if data.get("last_result_at")
                else None
            ),
        )


@dataclass(slots=True)
class HomeostasisState:
    curiosity: float = 0.3
    social_hunger: float = 0.2
    rest_need: float = 0.1
    reflection_need: float = 0.0
    care_impulse: float = 0.0
    overstimulation: float = 0.0
    current_phase: Phase = Phase.AMBIENT
    phase_entered_at: datetime = field(default_factory=utc_now)
    last_tick_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "curiosity": self.curiosity,
            "social_hunger": self.social_hunger,
            "rest_need": self.rest_need,
            "reflection_need": self.reflection_need,
            "care_impulse": self.care_impulse,
            "overstimulation": self.overstimulation,
            "current_phase": self.current_phase.value,
            "phase_entered_at": _serialize_datetime(self.phase_entered_at),
            "last_tick_at": _serialize_datetime(self.last_tick_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HomeostasisState:
        return cls(
            curiosity=float(data["curiosity"]),
            social_hunger=float(data["social_hunger"]),
            rest_need=float(data["rest_need"]),
            reflection_need=float(data["reflection_need"]),
            care_impulse=float(data["care_impulse"]),
            overstimulation=float(data["overstimulation"]),
            current_phase=Phase(data["current_phase"]),
            phase_entered_at=_deserialize_datetime(data["phase_entered_at"]),
            last_tick_at=_deserialize_datetime(data["last_tick_at"]),
        )


@dataclass(slots=True)
class CompanionState:
    version: int = 1
    homeostasis: HomeostasisState = field(default_factory=HomeostasisState)
    presence: PresenceState = PresenceState.SILENT
    mood: float = 0.0
    tick_count: int = 0
    perception: PerceptionSignals = field(default_factory=PerceptionSignals)
    perception_window: PerceptionWindowState = field(
        default_factory=PerceptionWindowState
    )
    user_presence: UserPresenceState = field(default_factory=UserPresenceState)
    initiative: InitiativeState = field(default_factory=InitiativeState)
    temperament: TemperamentProfile = field(default_factory=TemperamentProfile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "homeostasis": self.homeostasis.to_dict(),
            "presence": self.presence.value,
            "mood": self.mood,
            "tick_count": self.tick_count,
            "perception": self.perception.to_dict(),
            "perception_window": self.perception_window.to_dict(),
            "user_presence": self.user_presence.to_dict(),
            "initiative": self.initiative.to_dict(),
            "temperament": asdict(self.temperament),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompanionState:
        return cls(
            version=int(data["version"]),
            homeostasis=HomeostasisState.from_dict(data["homeostasis"]),
            presence=PresenceState(data["presence"]),
            mood=float(data["mood"]),
            tick_count=int(data["tick_count"]),
            perception=PerceptionSignals.from_dict(data.get("perception", {})),
            perception_window=PerceptionWindowState.from_dict(
                data.get("perception_window", {})
            ),
            user_presence=UserPresenceState.from_dict(data.get("user_presence", {})),
            initiative=InitiativeState.from_dict(data.get("initiative", {})),
            temperament=TemperamentProfile(**data["temperament"]),
        )

    @classmethod
    def from_json(cls, payload: str) -> CompanionState:
        return cls.from_dict(json.loads(payload))
