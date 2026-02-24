"""Connection verification step."""

import asyncio
from pathlib import Path

from dotenv import dotenv_values

from core.config_check import get_current_env
from onboarding.provider_probe import probe_all
from onboarding.state import WizardState
from onboarding.ui import STYLE


def run_verify_step(
    state: WizardState,
    project_root: Path,
    env_path: Path,
) -> bool:
    """Verify provider connections.

    Returns True to proceed (write config), False to retry from provider step.
    """
    env_vars = dict(dotenv_values(env_path)) if env_path.exists() else {}
    env_vars.update(state.env_vars)
    env_vars.update(get_current_env())

    print("\nVerifying connections...\n")

    results = asyncio.run(probe_all(state.providers, env_vars))

    all_ok = True
    for pid, (ok, msg) in results.items():
        symbol = "✓" if ok else "✗"
        print(f"  {symbol} {pid} — {msg}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\nAll providers verified.")
        return True

    print("\nSome providers failed verification.")
    return not _ask_retry_or_skip()


def _ask_retry_or_skip() -> bool:
    """Ask user: retry (True) or skip and write anyway (False)."""
    try:
        from questionary import Choice, select

        choice = select(
            "What would you like to do?",
            choices=[
                Choice("Retry (re-enter credentials)", "retry"),
                Choice("Skip verification and write config anyway", "skip"),
            ],
            style=STYLE,
        ).ask()
        return choice == "retry"
    except Exception:
        return False
