"""Structured diagnostics for extension admission and runtime failures."""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

DiagnosticPhase = Literal[
    "load",
    "config_validate",
    "initialize",
    "start",
    "health_check",
]
DiagnosticReason = Literal[
    "import_error",
    "config_invalid",
    "init_error",
    "start_error",
    "dependency_failed",
    "health_check_failed",
]


@dataclass(frozen=True)
class ExtensionDiagnostic:
    """One structured diagnostic record for an extension."""

    extension_id: str
    phase: DiagnosticPhase
    reason: DiagnosticReason
    message: str
    exception_type: str | None = None
    traceback: str = ""
    dependency_chain: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-serializable payload."""
        return asdict(self)
