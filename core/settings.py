"""Load application settings from config/settings.yaml."""

from pathlib import Path
from typing import Any

import yaml

_DEFAULTS: dict[str, Any] = {
    "supervisor": {
        "restart_file": "sandbox/.restart_requested",
        "restart_file_check_interval": 5,
    },
    "agents": {
        "orchestrator": {
            "model": "gpt-5.2",
            "base_url": "https://api.openai.com/v1",
            "instructions": "",
        },
    },
}

_cached: dict[str, Any] | None = None


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


def get_setting(settings: dict[str, Any], path: str, default: Any = None) -> Any:
    """Get a nested value by dot path (e.g. 'supervisor.restart_file')."""
    current: Any = settings
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def load_settings(config_dir: Path | None = None) -> dict[str, Any]:
    """Load settings from config/settings.yaml. Returns merged defaults + file values."""
    global _cached
    if _cached is not None:
        return _cached

    if config_dir is None:
        config_dir = Path(__file__).resolve().parent.parent / "config"
    path = config_dir / "settings.yaml"

    result: dict[str, Any] = {}
    for k, v in _DEFAULTS.items():
        result[k] = _deep_copy_nested(v)

    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _deep_merge(result, data)
        except (yaml.YAMLError, OSError):
            pass

    _cached = result
    return result


def _deep_copy_nested(obj: Any) -> Any:
    """Return a deep copy of nested dicts/lists for defaults."""
    if isinstance(obj, dict):
        return {k: _deep_copy_nested(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy_nested(x) for x in obj]
    return obj
