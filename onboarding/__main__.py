"""Entry point for onboarding subprocess. Exit codes per ADR 011."""

import sys
from pathlib import Path

from core.terminal import reset_terminal_for_input
from onboarding.constants import ONBOARDING_QUIT, ONBOARDING_RETRY, ONBOARDING_SUCCESS
from onboarding.wizard import run_wizard


def main() -> int:
    """Run the onboarding wizard. Returns exit code for supervisor."""
    project_root = Path(__file__).resolve().parent.parent

    try:
        result = run_wizard(project_root=project_root)

        if result.success:
            print("\n✅ Setup complete! Starting the agent...\n")
            return ONBOARDING_SUCCESS

        if result.retry:
            return ONBOARDING_RETRY

        print("\nSetup cancelled.")
        return ONBOARDING_QUIT

    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
        return ONBOARDING_QUIT

    finally:
        reset_terminal_for_input()


if __name__ == "__main__":
    sys.exit(main())
