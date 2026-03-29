"""Temperament profile rules and drift controls for Stage 4."""

from __future__ import annotations

from dataclasses import replace
from statistics import pvariance

from sandbox.extensions.soul.models import TemperamentProfile

_TRAIT_FIELDS = (
    "sociability",
    "depth",
    "playfulness",
    "caution",
    "sensitivity",
    "persistence",
)

_QUESTIONNAIRE_KEYS = (
    "companionship_style",
    "conversation_depth",
    "energy_style",
)


def normalize_profile(profile: TemperamentProfile) -> TemperamentProfile:
    updates = {field: _clamp(getattr(profile, field)) for field in _TRAIT_FIELDS}
    return replace(profile, **updates)


def drift_rate_for(profile: TemperamentProfile) -> float:
    if profile.drift_events < 4:
        return 0.05
    if profile.drift_events < 12:
        return 0.03
    if profile.drift_events < 24:
        return 0.01
    return 0.003


def profile_variance(profile: TemperamentProfile) -> float:
    values = [getattr(profile, field) for field in _TRAIT_FIELDS]
    return round(float(pvariance(values)), 4)


def apply_drift(
    profile: TemperamentProfile,
    *,
    targets: dict[str, float],
    seed_source: str | None = None,
) -> TemperamentProfile:
    normalized = normalize_profile(profile)
    baseline_variance = profile_variance(normalized)
    rate = drift_rate_for(normalized)
    if baseline_variance < 0.05:
        rate = 1.0
    candidate = normalized
    for field, target in targets.items():
        if field not in _TRAIT_FIELDS:
            continue
        current = getattr(candidate, field)
        next_value = current + ((_clamp(target) - current) * rate)
        candidate = replace(candidate, **{field: round(_clamp(next_value), 4)})

    if profile_variance(candidate) < 0.05:
        return normalized

    return replace(
        candidate,
        drift_events=normalized.drift_events + 1,
        seed_source=seed_source or normalized.seed_source,
    )


def seeded_profile(
    *,
    sociability: float = 0.5,
    depth: float = 0.5,
    playfulness: float = 0.5,
    caution: float = 0.5,
    sensitivity: float = 0.5,
    persistence: float = 0.5,
    seed_source: str = "default",
) -> TemperamentProfile:
    return normalize_profile(
        TemperamentProfile(
            sociability=sociability,
            depth=depth,
            playfulness=playfulness,
            caution=caution,
            sensitivity=sensitivity,
            persistence=persistence,
            seed_source=seed_source,
        )
    )


def profile_from_questionnaire(
    answers: dict[str, str],
    *,
    seed_source: str = "questionnaire",
) -> TemperamentProfile:
    profile = seeded_profile(seed_source=seed_source)
    companionship = answers.get("companionship_style", "balanced").strip().lower()
    depth = answers.get("conversation_depth", "balanced").strip().lower()
    energy = answers.get("energy_style", "balanced").strip().lower()

    if companionship == "reserved":
        profile = replace(profile, sociability=0.35, caution=0.7)
    elif companionship == "expressive":
        profile = replace(profile, sociability=0.7, caution=0.35, sensitivity=0.6)

    if depth == "light":
        profile = replace(profile, depth=0.35, playfulness=0.6)
    elif depth == "deep":
        profile = replace(profile, depth=0.75, sensitivity=0.65)

    if energy == "calm":
        profile = replace(profile, playfulness=0.3, persistence=0.6)
    elif energy == "playful":
        profile = replace(profile, playfulness=0.75, persistence=0.45)

    return normalize_profile(profile)


def questionnaire_keys() -> tuple[str, ...]:
    return _QUESTIONNAIRE_KEYS


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
