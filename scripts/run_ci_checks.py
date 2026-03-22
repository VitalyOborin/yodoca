"""Run repository CI checks locally in a blocking mode.

This script is used by the pre-commit hook to ensure all CI checks pass
before creating a commit.
"""

from __future__ import annotations

import subprocess
import sys

CHECKS: list[tuple[str, list[str]]] = [
    ("Ruff lint", ["uv", "run", "ruff", "check", "."]),
    ("Ruff format check", ["uv", "run", "ruff", "format", ".", "--check"]),
    ("Import boundaries", ["uv", "run", "lint-imports"]),
    (
        "Extension boundary check",
        ["uv", "run", "python", "scripts/check_extension_boundaries.py"],
    ),
    (
        "Extension context boundary check",
        ["uv", "run", "python", "scripts/check_extension_context_boundary.py"],
    ),
    (
        "Max LOC check",
        [
            "uv",
            "run",
            "python",
            "scripts/check_max_loc.py",
            "--allowlist",
            "scripts/max_loc_allowlist.txt",
        ],
    ),
    ("Dead code (vulture)", ["uv", "run", "vulture", "--config", "pyproject.toml"]),
    ("MyPy", ["uv", "run", "mypy", "core", "onboarding", "supervisor"]),
    ("Pytest", ["uv", "run", "pytest"]),
]


def main() -> int:
    for title, cmd in CHECKS:
        print(f"\n==> {title}")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(f"\nFAILED: {title}", file=sys.stderr)
            return result.returncode
    print("\nAll CI checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
