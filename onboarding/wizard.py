"""Onboarding wizard orchestration."""

from dataclasses import dataclass
from pathlib import Path

from onboarding.config_writer import write_config
from onboarding.state import WizardState
from onboarding.steps.embedding_step import run_embedding_step
from onboarding.steps.provider_step import run_provider_step
from onboarding.steps.verify_step import run_verify_step


@dataclass
class WizardResult:
    """Result of running the wizard."""

    success: bool
    retry: bool  # True if verification failed and user chose retry


def run_wizard(project_root: Path | None = None) -> WizardResult:
    """Run the full onboarding wizard.

    Returns WizardResult(success=True) when config was written.
    Returns WizardResult(success=False, retry=True) when user chose to retry after failed verification.
    Returns WizardResult(success=False, retry=False) when user cancelled.
    """
    root = project_root or Path.cwd()
    settings_path = root / "config" / "settings.yaml"
    env_path = root / ".env"

    while True:
        state = WizardState()

        if not run_provider_step(state):
            return WizardResult(success=False, retry=False)

        if not run_embedding_step(state):
            return WizardResult(success=False, retry=False)

        if not run_verify_step(state, root, env_path):
            continue

        write_config(state, settings_path, env_path, root)
        return WizardResult(success=True, retry=False)
