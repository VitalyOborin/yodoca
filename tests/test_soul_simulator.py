from sandbox.extensions.soul.simulator import run_simulation


def test_chatty_profile_has_phase_diversity_without_anomalies() -> None:
    summary = run_simulation(days=7, profile="chatty", seed=7)

    assert len(summary.phase_counts) >= 3
    assert summary.anomaly_counts.get("stuck_phase", 0) == 0
    assert summary.anomaly_counts.get("oscillation", 0) == 0


def test_silent_profile_remains_stable_over_24h() -> None:
    summary = run_simulation(days=1, profile="silent", seed=11)

    assert summary.ticks > 0
    assert summary.final_phase in summary.phase_counts
    assert summary.anomaly_counts.get("oscillation", 0) == 0


def test_erratic_profile_survives_30_day_run() -> None:
    summary = run_simulation(days=30, profile="erratic", seed=3)

    assert len(summary.phase_counts) >= 3
    assert summary.anomaly_counts.get("stuck_phase", 0) == 0
