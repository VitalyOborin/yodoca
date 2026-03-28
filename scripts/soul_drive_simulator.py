"""CLI entry point for soul drive dynamics simulation."""

from __future__ import annotations

import argparse
import json

from sandbox.extensions.soul.simulator import run_simulation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run soul drive dynamics simulation.")
    parser.add_argument("--days", type=int, default=7, help="Number of synthetic days.")
    parser.add_argument(
        "--profile",
        choices=["chatty", "silent", "erratic"],
        default="chatty",
        help="Synthetic user profile.",
    )
    parser.add_argument("--seed", type=int, default=0, help="Random seed.")
    parser.add_argument(
        "--tick-minutes",
        type=int,
        default=5,
        help="Tick interval in minutes.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run_simulation(
        days=args.days,
        profile=args.profile,
        seed=args.seed,
        tick_minutes=args.tick_minutes,
    )
    payload = {
        "days": summary.days,
        "ticks": summary.ticks,
        "profile": summary.profile,
        "phase_counts": summary.phase_counts,
        "anomaly_counts": summary.anomaly_counts,
        "event_counts": summary.event_counts,
        "final_phase": summary.final_phase,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(f"Profile: {summary.profile}")
    print(f"Days: {summary.days}")
    print(f"Ticks: {summary.ticks}")
    print("Phase counts:")
    for phase, count in sorted(summary.phase_counts.items()):
        print(f"  {phase}: {count}")
    print("Event counts:")
    for event, count in sorted(summary.event_counts.items()):
        print(f"  {event}: {count}")
    print("Anomalies:")
    if summary.anomaly_counts:
        for anomaly, count in sorted(summary.anomaly_counts.items()):
            print(f"  {anomaly}: {count}")
    else:
        print("  none")
    print(f"Final phase: {summary.final_phase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
