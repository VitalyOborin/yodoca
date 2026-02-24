"""Shared config validation for supervisor and onboarding.

Determines whether the application is sufficiently configured to start core.
"""

from pathlib import Path

import yaml
from dotenv import dotenv_values


def is_configured(
    settings_path: Path | None = None,
    env_path: Path | None = None,
    project_root: Path | None = None,
) -> tuple[bool, str]:
    """Check whether config is sufficient to start core.

    Validation logic:
    1. settings.yaml must exist and parse without errors.
    2. At least one provider must be configured under providers.
    3. For each provider: either api_key_literal is set, or api_key_secret
       references an env var that is present and non-empty.
    4. agents.default must reference a configured provider.

    Args:
        settings_path: Path to config/settings.yaml. If None, derived from project_root.
        env_path: Path to .env. If None, derived from project_root.
        project_root: Project root directory. Used when paths are None.
                     Defaults to cwd if all paths are None.

    Returns:
        (ok, reason) — ok is True if config is sufficient, reason explains why not.
    """
    root = project_root or Path.cwd()
    settings_file = settings_path or (root / "config" / "settings.yaml")
    env_file = env_path or (root / ".env")

    if not settings_file.exists():
        return False, "config/settings.yaml not found"

    try:
        settings = yaml.safe_load(settings_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return False, f"settings.yaml parse error: {e}"

    providers = settings.get("providers") or {}
    if not providers:
        return False, "No providers configured"

    env_vars = dict(dotenv_values(env_file)) if env_file.exists() else {}
    env_vars.update(get_current_env())

    def _provider_has_key(cfg: dict) -> bool:
        if cfg.get("api_key_literal"):
            return True
        secret = cfg.get("api_key_secret")
        return bool(secret and env_vars.get(secret))

    default_agent = (settings.get("agents") or {}).get("default")
    if not default_agent or not isinstance(default_agent, dict):
        return False, "agents.default not configured"

    default_provider = default_agent.get("provider")
    if not default_provider:
        return False, "agents.default.provider not set"

    if default_provider not in providers:
        return False, f"agents.default references unknown provider {default_provider!r}"

    default_provider_cfg = providers.get(default_provider)
    if not isinstance(default_provider_cfg, dict) or not _provider_has_key(
        default_provider_cfg
    ):
        return False, f"Provider {default_provider!r} has no API key set"

    return True, "ok"


def get_current_env() -> dict[str, str]:
    """Return current process environment as dict (for keys already in env)."""
    import os

    return {k: v for k, v in os.environ.items() if isinstance(v, str)}
