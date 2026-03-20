"""Load application settings from config/settings.yaml."""

import sys
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from core.settings_models import AppSettings

_cached: AppSettings | None = None


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge overlay into base recursively. Mutates base."""
    for key, value in overlay.items():
        if value is None:
            continue
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def format_validation_errors(exc: ValidationError, prefix: str = "") -> str:
    """Format Pydantic errors as path -> message lines."""
    lines: list[str] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        path_parts = [str(p) for p in loc]
        path = " -> ".join(path_parts)
        if prefix:
            path = f"{prefix}{path}" if path else prefix.rstrip(".")
        msg = err.get("msg", "validation error")
        lines.append(f"  {path}: {msg}")
    return "\n".join(lines)


def merge_settings_dict(config_dir: Path | None = None) -> dict[str, Any]:
    """Deep-merge AppSettings defaults with config/settings.yaml. No validation.

    Raises:
        ValueError: If YAML is missing, invalid, or not a mapping at top level.
        OSError: If the file cannot be read.
    """
    if config_dir is None:
        config_dir = Path(__file__).resolve().parent.parent / "config"
    path = config_dir / "settings.yaml"

    merged = AppSettings().model_dump(mode="python")

    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("settings.yaml must be a mapping at the top level")
        _deep_merge(merged, cast(dict[str, Any], raw))

    return merged


def validate_app_settings(merged: dict[str, Any]) -> AppSettings:
    """Validate merged settings dict. Raises ValidationError on failure."""
    return AppSettings.model_validate(merged)


def get_default_settings() -> dict[str, Any]:
    """Return default settings as dict. Used by onboarding config writer."""
    return AppSettings().model_dump(mode="python")


def get_setting(
    settings: AppSettings | dict[str, Any], path: str, default: Any = None
) -> Any:
    """Get a nested value by dot path (e.g. 'supervisor.restart_file')."""
    if isinstance(settings, AppSettings):
        current: Any = settings.model_dump(mode="python")
    else:
        current = settings
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def reload_settings() -> None:
    """Clear the settings cache. Call after config files change (e.g. onboarding)."""
    global _cached
    _cached = None


def load_settings(config_dir: Path | None = None) -> AppSettings:
    """Load settings from config/settings.yaml. Returns merged defaults + file values."""
    global _cached
    if _cached is not None:
        return _cached

    try:
        merged = merge_settings_dict(config_dir)
    except yaml.YAMLError as e:
        print(f"Configuration error: settings.yaml parse error:\n{e}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        _cached = validate_app_settings(merged)
    except ValidationError as e:
        print("Configuration error: invalid settings.yaml", file=sys.stderr)
        print(format_validation_errors(e, prefix=""), file=sys.stderr)
        sys.exit(1)

    return _cached
