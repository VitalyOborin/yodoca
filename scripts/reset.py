#!/usr/bin/env python3
"""
Reset AI agent state by removing persistent storage files and saved secrets.
Run from project root or any directory; paths are resolved relative to this script.
"""

from pathlib import Path
import re
import sys

# Make project imports available when executing as: python scripts/reset.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Paths relative to project root (parent of scripts/)
MEMORY_DIR = "sandbox/data/memory"  # all files in this dir are removed
OTHER_FILES = [
    ".env",
    "sandbox/logs/app.log",
    "config/settings.yaml",
    "sandbox/data/scheduler/scheduler.db",
    "sandbox/data/event_journal.db",
    "sandbox/data/task_engine/task_engine.db",
    "sandbox/data/kv/values.json",
]
SETTINGS_FILE = "config/settings.yaml"
ENV_FILE = ".env"

_SETTINGS_SECRET_RE = re.compile(
    r'^\s*[A-Za-z0-9_]+_secret:\s*["\']?([A-Za-z_][A-Za-z0-9_]*)["\']?\s*$'
)
_ENV_KEY_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


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


def _collect_secrets_from_settings(path: Path) -> set[str]:
    names: set[str] = set()
    if not path.exists():
        return names
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            match = _SETTINGS_SECRET_RE.match(raw_line)
            if match:
                names.add(match.group(1))
    except OSError:
        pass
    return names


def _collect_env_keys(path: Path) -> set[str]:
    names: set[str] = set()
    if not path.exists():
        return names
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _ENV_KEY_RE.match(line)
            if match:
                names.add(match.group(1))
    except OSError:
        pass
    return names


def _clear_saved_secrets(root: Path) -> tuple[int, list[tuple[str, Exception]]]:
    settings_path = root / SETTINGS_FILE
    env_path = root / ENV_FILE
    secret_names = _collect_secrets_from_settings(settings_path) | _collect_env_keys(
        env_path
    )
    removed = 0
    errors: list[tuple[str, Exception]] = []

    try:
        from core.secrets import delete_secret
    except ModuleNotFoundError:
        print("Skip keyring cleanup: keyring dependency is not installed.")
        return 0, errors

    for name in sorted(secret_names):
        try:
            delete_secret(name)
            print(f"Cleared secret: {name}")
            removed += 1
        except Exception as e:  # pragma: no cover - backend-specific failures
            errors.append((name, e))
            print(f"Error clearing secret {name}: {e}", file=sys.stderr)
    return removed, errors


def main() -> int:
    if not confirm():
        print("Aborted.")
        return 0

    root = get_project_root()
    removed = 0
    errors = []

    secrets_removed, secret_errors = _clear_saved_secrets(root)
    removed += secrets_removed
    for name, error in secret_errors:
        errors.append((root / f"<secret:{name}>", error))

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
                    print(
                        f"Error removing {path.relative_to(root)}: {e}", file=sys.stderr
                    )
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
