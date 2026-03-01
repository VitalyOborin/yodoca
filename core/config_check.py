"""Shared config validation for supervisor and onboarding.

Determines whether the application is sufficiently configured to start core.
"""

from pathlib import Path
from typing import Callable

import yaml
from dotenv import dotenv_values

from core import secrets


def _provider_has_key(cfg: dict, env_vars: dict[str, str]) -> bool:
    if cfg.get("api_key_literal"):
        return True
    secret = cfg.get("api_key_secret")
    if not secret:
        return False
    return bool(secrets.get_secret(secret) or env_vars.get(secret))


def _check_default_agent(
    settings: dict,
    providers: dict,
    provider_has_key: Callable[[dict], bool],
) -> tuple[bool, str]:
    default_agent = (settings.get("agents") or {}).get("default")
    if not default_agent or not isinstance(default_agent, dict):
        return False, "agents.default not configured"
    default_provider = default_agent.get("provider")
    if not default_provider:
        return False, "agents.default.provider not set"
    if default_provider not in providers:
        return False, f"agents.default references unknown provider {default_provider!r}"
    default_provider_cfg = providers.get(default_provider)
    if not isinstance(default_provider_cfg, dict) or not provider_has_key(
        default_provider_cfg
    ):
        return False, f"Provider {default_provider!r} has no API key set"
    return True, "ok"


def _read_settings(settings_file: Path) -> tuple[dict, str | None]:
    """Load and parse settings YAML. Returns (settings, None) or ({}, error_message)."""
    try:
        data = yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {}
        return (data, None)
    except yaml.YAMLError as e:
        return ({}, f"settings.yaml parse error: {e}")


def is_configured(
    settings_path: Path | None = None,
    env_path: Path | None = None,
    project_root: Path | None = None,
) -> tuple[bool, str]:
    """Check whether config is sufficient to start core. Returns (ok, reason)."""
    root = project_root or Path.cwd()
    settings_file = settings_path or (root / "config" / "settings.yaml")
    env_file = env_path or (root / ".env")
    if not settings_file.exists():
        return False, "config/settings.yaml not found"
    settings, err = _read_settings(settings_file)
    if err is not None:
        return False, err
    providers = settings.get("providers") or {}
    if not providers:
        return False, "No providers configured"
    env_vars = dict(dotenv_values(env_file)) if env_file.exists() else {}
    env_vars.update(get_current_env())
    has_key = lambda cfg: _provider_has_key(cfg, env_vars)
    return _check_default_agent(settings, providers, has_key)


def get_current_env() -> dict[str, str]:
    """Return current process environment as dict (for keys already in env)."""
    import os

    return {k: v for k, v in os.environ.items() if isinstance(v, str)}
