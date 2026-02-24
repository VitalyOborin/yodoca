"""Exit codes for onboarding subprocess (ADR 011)."""

ONBOARDING_SUCCESS = 0  # Config written, supervisor will launch core
ONBOARDING_QUIT = 1  # User cancelled (Ctrl+C)
ONBOARDING_RETRY = 2  # Verification failed, retry wizard
