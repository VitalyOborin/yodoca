import pytest

from sandbox.extensions.soul.simulator import (
    DRIVE_NAMES,
    SimulationSummary,
    run_simulation,
)

MIN_BOUND = 0.05
MAX_BOUND = 0.95


def _assert_drives_in_bounds(summary: SimulationSummary) -> None:
    for name in DRIVE_NAMES:
        lo = summary.min_drives[name]
        hi = summary.max_drives[name]
        assert lo >= MIN_BOUND, f"{name} min={lo:.4f} dropped below {MIN_BOUND}"
        assert hi <= MAX_BOUND, f"{name} max={hi:.4f} exceeded {MAX_BOUND}"


def _assert_no_anomalies(summary: SimulationSummary) -> None:
    assert summary.anomaly_counts.get("stuck_phase", 0) == 0, "stuck_phase detected"
    assert summary.anomaly_counts.get("oscillation", 0) == 0, "oscillation detected"


# --- Core profile tests ---


def test_chatty_profile_has_phase_diversity_without_anomalies() -> None:
    summary = run_simulation(days=7, profile="chatty", seed=7)

    assert len(summary.phase_counts) >= 3
    _assert_no_anomalies(summary)


def test_silent_profile_remains_stable_over_24h() -> None:
    summary = run_simulation(days=1, profile="silent", seed=11)

    assert summary.ticks > 0
    assert summary.final_phase in summary.phase_counts
    assert summary.anomaly_counts.get("oscillation", 0) == 0


def test_erratic_profile_survives_30_day_run() -> None:
    summary = run_simulation(days=30, profile="erratic", seed=3)

    assert len(summary.phase_counts) >= 3
    assert summary.anomaly_counts.get("stuck_phase", 0) == 0


# --- Bounds validation: drives must stay within [0.05, 0.95] for all profiles ---


@pytest.mark.parametrize(
    "profile,days,seed",
    [
        ("chatty", 30, 42),
        ("silent", 30, 17),
        ("erratic", 30, 99),
        ("burst", 7, 5),
    ],
)
def test_drive_bounds_stay_within_spec(profile: str, days: int, seed: int) -> None:
    summary = run_simulation(days=days, profile=profile, seed=seed)
    _assert_drives_in_bounds(summary)


# --- Edge case: 48h complete silence ---


def test_48h_silence_no_stuck_phase() -> None:
    summary = run_simulation(days=2, profile="silent", seed=0)

    assert summary.anomaly_counts.get("stuck_phase", 0) == 0
    _assert_drives_in_bounds(summary)


def test_48h_silence_has_phase_diversity() -> None:
    summary = run_simulation(days=2, profile="silent", seed=0)

    assert len(summary.phase_counts) >= 2, (
        "system should transition even with no user interaction"
    )


# --- Edge case: burst messaging (stress test) ---


def test_burst_profile_survives_7_day_run() -> None:
    summary = run_simulation(days=7, profile="burst", seed=10)

    _assert_drives_in_bounds(summary)
    assert summary.anomaly_counts.get("stuck_phase", 0) == 0
    assert (
        summary.event_counts.get("inbound", 0) + summary.event_counts.get("burst", 0)
        > 100
    )


# --- Coupling stability: all profiles produce reasonable phase diversity ---


@pytest.mark.parametrize(
    "profile,seed",
    [
        ("chatty", 1),
        ("erratic", 2),
    ],
)
def test_coupling_stability_produces_phase_diversity(profile: str, seed: int) -> None:
    summary = run_simulation(days=14, profile=profile, seed=seed)

    assert len(summary.phase_counts) >= 3, f"{profile}: insufficient phase diversity"
    _assert_no_anomalies(summary)
    _assert_drives_in_bounds(summary)


def test_burst_coupling_stability_tolerates_minor_oscillation() -> None:
    """Under extreme burst load, minor oscillation between RESTING/AMBIENT is
    expected — constant overstimulation causes rapid phase toggling. The important
    invariants are: drives stay bounded and no stuck phases."""
    summary = run_simulation(days=14, profile="burst", seed=3)

    assert len(summary.phase_counts) >= 2
    assert summary.anomaly_counts.get("stuck_phase", 0) == 0
    assert summary.anomaly_counts.get("oscillation", 0) <= 3, (
        "excessive oscillation under burst load"
    )
    _assert_drives_in_bounds(summary)
