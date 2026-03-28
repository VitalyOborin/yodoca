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


@dataclass(slots=True)
class TemperamentProfile:
    sociability: float = 0.5
    depth: float = 0.5
    playfulness: float = 0.5
    caution: float = 0.5
    sensitivity: float = 0.5
    persistence: float = 0.5


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
    temperament: TemperamentProfile = field(default_factory=TemperamentProfile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "homeostasis": self.homeostasis.to_dict(),
            "presence": self.presence.value,
            "mood": self.mood,
            "tick_count": self.tick_count,
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
            temperament=TemperamentProfile(**data["temperament"]),
        )

    @classmethod
    def from_json(cls, payload: str) -> CompanionState:
        return cls.from_dict(json.loads(payload))
