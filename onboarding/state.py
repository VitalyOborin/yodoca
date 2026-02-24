"""Shared wizard state."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WizardState:
    """Mutable state collected during the onboarding wizard."""

    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    env_vars: dict[str, str] = field(default_factory=dict)
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    extensions: dict[str, dict[str, Any]] = field(default_factory=dict)
