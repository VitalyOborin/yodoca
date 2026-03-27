"""Tool to inspect extension status and diagnostics."""

from collections.abc import Callable
from typing import Any

from agents import function_tool
from pydantic import BaseModel, Field


class ExtensionDoctorDiagnostic(BaseModel):
    extension_id: str
    phase: str
    reason: str
    message: str
    exception_type: str | None = None
    traceback: str = ""
    dependency_chain: list[str] = Field(default_factory=list)
    created_at: str


class ExtensionDoctorEntry(BaseModel):
    extension_id: str
    name: str
    state: str
    status: str
    entrypoint: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    latest_diagnostic: ExtensionDoctorDiagnostic | None = None


class ExtensionsDoctorResult(BaseModel):
    success: bool
    summary: str
    counts: dict[str, int]
    extensions: list[ExtensionDoctorEntry]


def _build_summary(report: dict[str, Any]) -> str:
    counts = report.get("counts", {})
    active = counts.get("active", 0)
    inactive = counts.get("inactive", 0)
    errors = counts.get("error", 0)
    return f"Extensions: {active} active, {inactive} inactive, {errors} error"


def make_extensions_doctor_tool(
    report_getter: Callable[[], dict[str, Any]],
) -> Any:
    """Create extensions_doctor tool bound to the current Loader report getter."""

    @function_tool(name_override="extensions_doctor")
    def extensions_doctor() -> ExtensionsDoctorResult:
        """Inspect all discovered extensions and their latest diagnostics."""
        report = report_getter()
        return ExtensionsDoctorResult(
            success=True,
            summary=_build_summary(report),
            counts=report.get("counts", {}),
            extensions=report.get("extensions", []),
        )

    return extensions_doctor
