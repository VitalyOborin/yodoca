from sandbox.extensions.soul.models import TemperamentProfile
from sandbox.extensions.soul.temperament import (
    apply_drift,
    drift_rate_for,
    profile_variance,
    seeded_profile,
)


def test_seeded_profile_uses_clamped_defaults() -> None:
    profile = seeded_profile(
        sociability=1.2,
        depth=0.8,
        playfulness=-0.1,
        seed_source="questionnaire",
    )

    assert profile.sociability == 1.0
    assert profile.playfulness == 0.0
    assert profile.seed_source == "questionnaire"


def test_drift_rate_decays_as_profile_matures() -> None:
    assert drift_rate_for(TemperamentProfile(drift_events=0)) == 0.05
    assert drift_rate_for(TemperamentProfile(drift_events=6)) == 0.03
    assert drift_rate_for(TemperamentProfile(drift_events=16)) == 0.01
    assert drift_rate_for(TemperamentProfile(drift_events=30)) == 0.003


def test_apply_drift_rejects_personality_erosion() -> None:
    profile = TemperamentProfile(
        sociability=0.8,
        depth=0.2,
        playfulness=0.7,
        caution=0.3,
        sensitivity=0.6,
        persistence=0.4,
    )

    drifted = apply_drift(
        profile,
        targets={field: 0.5 for field in (
            "sociability",
            "depth",
            "playfulness",
            "caution",
            "sensitivity",
            "persistence",
        )},
    )

    assert drifted == profile


def test_apply_drift_updates_profile_when_signal_is_meaningful() -> None:
    profile = TemperamentProfile(
        sociability=0.9,
        depth=0.1,
        playfulness=0.2,
        caution=0.8,
        sensitivity=0.3,
        persistence=0.9,
    )

    drifted = apply_drift(
        profile,
        targets={"playfulness": 0.9, "depth": 0.9},
        seed_source="usage",
    )

    assert drifted.playfulness > profile.playfulness
    assert drifted.depth > profile.depth
    assert drifted.drift_events == 1
    assert drifted.seed_source == "usage"
    assert profile_variance(drifted) >= 0.05


def test_flat_profile_uses_bootstrap_shift_for_first_identity_change() -> None:
    profile = TemperamentProfile()

    drifted = apply_drift(
        profile,
        targets={"depth": 0.9, "playfulness": 0.2, "caution": 0.8},
        seed_source="usage",
    )

    assert drifted != profile
    assert drifted.drift_events == 1
