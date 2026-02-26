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
        "default": {
            "provider": "openai",
            "model": "gpt-5",
        },
        # Orchestrator has no entry here: when absent from settings.yaml it uses agents.default
        # (ModelRouter.get_model("orchestrator") falls back to default). Optional overrides go in
        # settings.yaml as agents.orchestrator (instructions, model, etc.).
    },
    "providers": {},
    "event_bus": {
        "db_path": "sandbox/data/event_journal.db",
        "poll_interval": 5.0,
        "batch_size": 3,
        "max_retries": 3,
        "busy_timeout": 5000,
        "stale_timeout": 300,
    },
    "logging": {
        "file": "sandbox/logs/app.log",
        "level": "INFO",
        "log_to_console": False,
        "max_bytes": 10485760,  # 10 MB
        "backup_count": 3,
    },
    "session": {
        "timeout_sec": 1800,
    },
    "extensions": {},
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


def get_default_settings() -> dict[str, Any]:
    """Return a deep copy of default settings. Used by onboarding config writer."""
    return _deep_copy_nested(_DEFAULTS)


def get_setting(settings: dict[str, Any], path: str, default: Any = None) -> Any:
    """Get a nested value by dot path (e.g. 'supervisor.restart_file')."""
    current: Any = settings
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def reload_settings() -> None:
    """Clear the settings cache. Call after config files change (e.g. onboarding)."""
    global _cached
    _cached = None


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
