"""Shared config validation for supervisor and onboarding.

Determines whether the application is sufficiently configured to start core.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import ValidationError

from core import secrets
from core.settings import merge_settings_dict, validate_app_settings


def _provider_has_key(cfg: dict[str, Any], env_vars: dict[str, str]) -> bool:
    if cfg.get("api_key_literal"):
        return True
    secret = cfg.get("api_key_secret")
    if not secret:
        return False
    return bool(secrets.get_secret(secret) or env_vars.get(secret))


def _check_default_agent(
    settings_dict: dict[str, Any],
    providers: dict[str, Any],
    provider_has_key: Callable[[dict[str, Any]], bool],
) -> tuple[bool, str]:
    agent_id = (settings_dict.get("default_agent") or "").strip()
    if not agent_id:
        return False, "default_agent not configured in settings"

    agents = settings_dict.get("agents") or {}
    agent_cfg = agents.get(agent_id) or agents.get("default")
    if not agent_cfg or not isinstance(agent_cfg, dict):
        return (
            False,
            f"No model config for default_agent {agent_id!r} (agents.{agent_id} or agents.default)",
        )
    default_provider = agent_cfg.get("provider")
    if not default_provider:
        return False, f"agents.{agent_id}.provider not set"
    if default_provider not in providers:
        return (
            False,
            f"agents.{agent_id} references unknown provider {default_provider!r}",
        )
    default_provider_cfg = providers.get(default_provider)
    if not isinstance(default_provider_cfg, dict) or not provider_has_key(
        default_provider_cfg
    ):
        return False, f"Provider {default_provider!r} has no API key set"
    return True, "ok"


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

    config_dir = settings_file.parent
    try:
        merged = merge_settings_dict(config_dir)
    except (ValueError, OSError) as e:
        return False, str(e)
    except yaml.YAMLError as e:
        return False, f"settings.yaml parse error: {e}"

    try:
        validate_app_settings(merged)
    except ValidationError as e:
        from core.settings import format_validation_errors

        return False, "Invalid settings.yaml:\n" + format_validation_errors(e)

    providers = merged.get("providers") or {}
    if not providers:
        return False, "No providers configured"
    raw_env = dict(dotenv_values(env_file)) if env_file.exists() else {}
    env_vars = {k: v for k, v in raw_env.items() if isinstance(v, str)}
    env_vars.update(get_current_env())

    def has_key(cfg: dict[str, Any]) -> bool:
        return _provider_has_key(cfg, env_vars)

    return _check_default_agent(merged, providers, has_key)


def get_current_env() -> dict[str, str]:
    """Return current process environment as dict (for keys already in env)."""
    import os

    return {k: v for k, v in os.environ.items() if isinstance(v, str)}
