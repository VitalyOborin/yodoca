#!/usr/bin/env python3
"""Flag Python files under core/ and sandbox/extensions/ exceeding a line threshold."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _boundary_common import green, red, yellow

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_THRESHOLD = 500
SCAN_ROOTS = (ROOT / "core", ROOT / "sandbox" / "extensions")


def _count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _load_allowlist(path: Path | None) -> frozenset[str]:
    if path is None or not path.is_file():
        return frozenset()
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line.replace("\\", "/"))
    return frozenset(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-lines",
        type=int,
        default=DEFAULT_THRESHOLD,
        metavar="N",
        help=f"Maximum allowed lines per file (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        metavar="FILE",
        help="Optional file of repo-relative paths (forward slashes) to skip reporting",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="List all scanned files"
    )
    args = parser.parse_args()

    allowed_paths = _load_allowlist(args.allowlist)

    offenders: list[tuple[Path, int]] = []
    for base in SCAN_ROOTS:
        if not base.is_dir():
            print(yellow(f"Skip missing directory: {base.relative_to(ROOT)}"))
            continue
        for py_path in sorted(base.rglob("*.py")):
            if "__pycache__" in py_path.parts:
                continue
            rel_posix = py_path.relative_to(ROOT).as_posix()
            n = _count_lines(py_path)
            if args.verbose:
                print(f"  {n:4d}  {rel_posix}")
            if n > args.max_lines and rel_posix not in allowed_paths:
                offenders.append((py_path, n))

    offenders.sort(key=lambda x: (-x[1], str(x[0])))

    if offenders:
        print(red(f"Files exceeding {args.max_lines} lines:"))
        for path, n in offenders:
            print(f"  {n:4d}  {path.relative_to(ROOT)}")
        return 1

    print(green(f"Max LOC check passed (threshold {args.max_lines})."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
