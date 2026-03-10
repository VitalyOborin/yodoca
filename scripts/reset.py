#!/usr/bin/env python3
"""
Reset AI agent state by removing persistent storage files, logs, and saved secrets.
Run from project root or any directory; paths are resolved relative to this script.
"""

from pathlib import Path
import sys
import shutil

import yaml

# Make project imports available when executing as: python scripts/reset.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DATA_DIR = "sandbox/data"
LOGS_DIR = "sandbox/logs"
OTHER_FILES = [
    ".env",
    "config/settings.yaml",
]
ENV_FILE = ".env"
SETTINGS_FILE = "config/settings.yaml"


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


def _collect_env_keys(path: Path) -> set[str]:
    names: set[str] = set()
    if not path.exists():
        return names
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].removeprefix("export ").strip()
            if key:
                names.add(key)
    except OSError:
        pass
    return names


def _extract_secret_names(data: object) -> set[str]:
    names: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "secrets" and isinstance(value, list):
                names.update(item for item in value if isinstance(item, str) and item)
            if key.endswith("_secret") and isinstance(value, str) and value:
                names.add(value)
            names.update(_extract_secret_names(value))
    elif isinstance(data, list):
        for item in data:
            names.update(_extract_secret_names(item))
    return names


def _collect_secrets_from_yaml(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return set()
    return _extract_secret_names(data)


def _collect_manifest_secret_names(root: Path) -> set[str]:
    manifest_dir = root / "sandbox" / "extensions"
    if not manifest_dir.exists():
        return set()
    names: set[str] = set()
    for manifest_path in manifest_dir.rglob("manifest.yaml"):
        names.update(_collect_secrets_from_yaml(manifest_path))
    return names


def _clear_saved_secrets(root: Path) -> tuple[int, list[tuple[str, Exception]]]:
    settings_path = root / SETTINGS_FILE
    env_path = root / ENV_FILE
    errors: list[tuple[str, Exception]] = []

    try:
        from core import secrets as secrets_module
    except ModuleNotFoundError:
        print("Skip secret cleanup: secrets module is not available.")
        return 0, errors

    secret_names = (
        secrets_module.list_registered_secrets()
        | _collect_secrets_from_yaml(settings_path)
        | _collect_manifest_secret_names(root)
        | _collect_env_keys(env_path)
    )

    removed = 0
    for name in sorted(secret_names):
        try:
            secrets_module.delete_secret(name)
            print(f"Cleared secret: {name}")
            removed += 1
        except Exception as e:  # pragma: no cover - backend-specific failures
            errors.append((name, e))
            print(f"Error clearing secret {name}: {e}", file=sys.stderr)
    return removed, errors


def _clear_directory_contents(
    root: Path, relative_dir: str
) -> tuple[int, list[tuple[Path, Exception]]]:
    target = root / relative_dir
    removed = 0
    errors: list[tuple[Path, Exception]] = []

    if not target.exists():
        print(f"Skip (not found): {relative_dir}")
        return removed, errors

    for path in sorted(target.iterdir(), key=lambda item: item.name):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            print(f"Removed: {path.relative_to(root)}")
            removed += 1
        except OSError as e:
            errors.append((path, e))
            print(f"Error removing {path.relative_to(root)}: {e}", file=sys.stderr)
    return removed, errors


def _remove_files(
    root: Path, relative_paths: list[str]
) -> tuple[int, list[tuple[Path, Exception]]]:
    removed = 0
    errors: list[tuple[Path, Exception]] = []
    for rel in relative_paths:
        path = root / rel
        if not path.exists():
            print(f"Skip (not found): {path.relative_to(root)}")
            continue
        try:
            path.unlink()
            print(f"Removed: {path.relative_to(root)}")
            removed += 1
        except OSError as e:
            errors.append((path, e))
            print(f"Error removing {path.relative_to(root)}: {e}", file=sys.stderr)
    return removed, errors


def main() -> int:
    if not confirm():
        print("Aborted.")
        return 0

    root = get_project_root()
    removed = 0
    errors: list[tuple[Path, Exception]] = []

    secrets_removed, secret_errors = _clear_saved_secrets(root)
    removed += secrets_removed
    for name, error in secret_errors:
        errors.append((root / f"<secret:{name}>", error))

    for relative_dir in (DATA_DIR, LOGS_DIR):
        dir_removed, dir_errors = _clear_directory_contents(root, relative_dir)
        removed += dir_removed
        errors.extend(dir_errors)

    files_removed, file_errors = _remove_files(root, OTHER_FILES)
    removed += files_removed
    errors.extend(file_errors)

    if errors:
        print(f"\n{len(errors)} item(s) could not be removed.", file=sys.stderr)
        return 1
    print(f"\nDone. Removed {removed} item(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
