"""Atomic config write for settings.yaml and .env."""

import logging
import tempfile
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from core.secrets import is_keyring_available, set_secret
from core.settings import get_default_settings, get_setting, load_settings
from onboarding.state import WizardState

logger = logging.getLogger(__name__)


def _secret_keys_from_providers(providers: dict[str, Any]) -> set[str]:
    """Keys that are secrets (api_key_secret values from provider configs)."""
    out: set[str] = set()
    for cfg in providers.values():
        if isinstance(cfg, dict):
            secret = cfg.get("api_key_secret")
            if secret:
                out.add(secret)
    return out


def write_config(
    state: WizardState,
    settings_path: Path,
    env_path: Path,
    project_root: Path,
) -> None:
    """Atomically write settings.yaml and .env from wizard state."""
    base = get_default_settings()
    base["providers"] = state.providers
    base["agents"] = state.agents
    # Ensure default agent has explicit default instructions path
    if "default" in base["agents"] and isinstance(base["agents"]["default"], dict):
        base["agents"]["default"].setdefault("instructions", "prompts/default.jinja2")
    if state.extensions:
        base["extensions"] = {**(base.get("extensions") or {}), **state.extensions}
    _write_atomic_yaml(settings_path, base)

    secret_keys = _secret_keys_from_providers(state.providers)
    if is_keyring_available():
        for k, v in state.env_vars.items():
            if k in secret_keys:
                try:
                    set_secret(k, v)
                except Exception as e:
                    logger.warning("Failed to store %s in keyring: %s. Writing to .env.", k, e)
                    secret_keys = secret_keys - {k}
        env_to_write = {
            k: v
            for k, v in state.env_vars.items()
            if k not in secret_keys
        }
    else:
        logger.warning(
            "Keyring unavailable (headless/CI). Storing secrets in .env. "
            "Consider using .env for headless deployments."
        )
        env_to_write = state.env_vars

    _write_env(env_path, env_to_write, exclude_secrets=secret_keys if is_keyring_available() else set())

    settings = load_settings(project_root / "config")
    restart_rel = get_setting(settings, "supervisor.restart_file", "sandbox/.restart_requested")
    restart_file = project_root / restart_rel
    restart_file.parent.mkdir(parents=True, exist_ok=True)
    restart_file.touch()


def _write_atomic_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write YAML atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".yaml",
        prefix="settings_",
        dir=path.parent,
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_env(
    env_path: Path,
    new_vars: dict[str, str],
    *,
    exclude_secrets: set[str] | None = None,
) -> None:
    """Merge new env vars into .env, preserving existing unrelated keys.

    When exclude_secrets is set, keys in that set are stripped from existing
    .env before merge (so secrets stay in keyring only).
    """
    exclude = exclude_secrets or set()
    existing = dict(dotenv_values(env_path)) if env_path.exists() else {}
    existing = {k: v for k, v in existing.items() if k not in exclude}
    merged = {**existing, **new_vars}

    content = "\n".join(f"{k}={v}" for k, v in merged.items())
    if content and not content.endswith("\n"):
        content += "\n"

    env_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".env",
        prefix="env_",
        dir=env_path.parent,
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp).replace(env_path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
