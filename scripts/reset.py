#!/usr/bin/env python3
"""
Reset AI agent memory by removing persistent storage files.
Run from project root or any directory; paths are resolved relative to this script.
"""

from pathlib import Path
import sys


# Paths relative to project root (parent of scripts/)
MEMORY_DIR = "sandbox/data/memory"  # all files in this dir are removed
OTHER_FILES = [
    "sandbox/logs/app.log",
    "config/settings.yaml",
    "sandbox/data/scheduler/scheduler.db",
    "sandbox/data/event_journal.db",
    "sandbox/data/kv/values.json",
]


def get_project_root() -> Path:
    """Project root is the parent of the directory containing this script."""
    return Path(__file__).resolve().parent.parent


def confirm() -> bool:
    """Prompt until user enters Y (proceed) or n (abort). Returns True only for Y, False for n."""
    while True:
        answer = input("Are you sure? [Y/n]: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def main() -> int:
    if not confirm():
        print("Aborted.")
        return 0

    root = get_project_root()
    removed = 0
    errors = []

    memory_dir = root / MEMORY_DIR
    if memory_dir.is_dir():
        for path in memory_dir.rglob("*"):
            if path.is_file():
                try:
                    path.unlink()
                    print(f"Removed: {path.relative_to(root)}")
                    removed += 1
                except OSError as e:
                    errors.append((path, e))
                    print(f"Error removing {path.relative_to(root)}: {e}", file=sys.stderr)
    else:
        print(f"Skip (not found): {MEMORY_DIR}")

    for rel in OTHER_FILES:
        path = root / rel
        if path.exists():
            try:
                path.unlink()
                print(f"Removed: {path.relative_to(root)}")
                removed += 1
            except OSError as e:
                errors.append((path, e))
                print(f"Error removing {path.relative_to(root)}: {e}", file=sys.stderr)
        else:
            print(f"Skip (not found): {path.relative_to(root)}")

    if errors:
        print(f"\n{len(errors)} file(s) could not be removed.", file=sys.stderr)
        return 1
    print(f"\nDone. Removed {removed} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
