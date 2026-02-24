"""Atomic config write for settings.yaml and .env."""

import tempfile
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from core.settings import get_default_settings, get_setting, load_settings
from onboarding.state import WizardState


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

    _write_env(env_path, state.env_vars)

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


def _write_env(env_path: Path, new_vars: dict[str, str]) -> None:
    """Merge new env vars into .env, preserving existing unrelated keys."""
    existing = dict(dotenv_values(env_path)) if env_path.exists() else {}
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
