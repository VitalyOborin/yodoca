"""Bounded in-memory extension diagnostics and EventBus publishing."""

import traceback
from typing import Any

from core.events import EventBus
from core.events.topics import SystemTopics
from core.extensions.contract import ExtensionState
from core.extensions.loader.diagnostics import (
    DiagnosticPhase,
    DiagnosticReason,
    ExtensionDiagnostic,
)
from core.extensions.manifest import ExtensionManifest

_MAX_DIAGNOSTICS_PER_EXTENSION = 10


class DiagnosticsManager:
    """Records, stores, and queries extension diagnostics; publishes to EventBus."""

    def __init__(self) -> None:
        self._diagnostics: dict[str, list[ExtensionDiagnostic]] = {}
        self._event_bus: EventBus | None = None

    def set_event_bus(self, event_bus: EventBus | None) -> None:
        self._event_bus = event_bus

    def clear(self) -> None:
        self._diagnostics.clear()

    def _append_diagnostic(self, diagnostic: ExtensionDiagnostic) -> None:
        history = self._diagnostics.setdefault(diagnostic.extension_id, [])
        history.append(diagnostic)
        if len(history) > _MAX_DIAGNOSTICS_PER_EXTENSION:
            del history[:-_MAX_DIAGNOSTICS_PER_EXTENSION]

    async def _publish_diagnostic(self, diagnostic: ExtensionDiagnostic) -> None:
        if self._event_bus is None:
            return
        await self._event_bus.publish(
            SystemTopics.EXTENSION_ERROR,
            "loader",
            diagnostic.as_dict(),
        )

    async def record_diagnostic(
        self,
        ext_id: str,
        *,
        phase: DiagnosticPhase,
        reason: DiagnosticReason,
        message: str,
        exception: BaseException | None = None,
        traceback_text: str = "",
        dependency_chain: list[str] | None = None,
    ) -> None:
        diagnostic = ExtensionDiagnostic(
            extension_id=ext_id,
            phase=phase,
            reason=reason,
            message=message,
            exception_type=type(exception).__name__ if exception else None,
            traceback=traceback_text
            or (
                "".join(traceback.format_exception(exception))
                if exception is not None
                else ""
            ),
            dependency_chain=list(dependency_chain or []),
        )
        self._append_diagnostic(diagnostic)
        await self._publish_diagnostic(diagnostic)

    async def record_health_failure(
        self,
        ext_id: str,
        _exception_type: str | None,
        message: str,
        traceback_text: str,
    ) -> None:
        await self.record_diagnostic(
            ext_id,
            phase="health_check",
            reason="health_check_failed",
            message=message,
            traceback_text=traceback_text,
        )

    def get_extension_diagnostic(
        self, ext_id: str, latest_only: bool = True
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        history = self._diagnostics.get(ext_id, [])
        if not history:
            return None
        if latest_only:
            return history[-1].as_dict()
        return [entry.as_dict() for entry in history]

    def get_failed_extensions(
        self, state: dict[str, ExtensionState]
    ) -> dict[str, dict[str, Any]]:
        failed: dict[str, dict[str, Any]] = {}
        for ext_id, st in state.items():
            if st != ExtensionState.ERROR:
                continue
            latest = self.get_extension_diagnostic(ext_id)
            if isinstance(latest, dict):
                failed[ext_id] = latest
        return failed

    def get_extension_status_report(
        self,
        manifests: list[ExtensionManifest],
        state: dict[str, ExtensionState],
    ) -> dict[str, Any]:
        counts = {"active": 0, "inactive": 0, "error": 0}
        extensions: list[dict[str, Any]] = []
        for manifest in manifests:
            st = state.get(manifest.id, ExtensionState.INACTIVE)
            counts[st.value] = counts.get(st.value, 0) + 1
            latest = self.get_extension_diagnostic(manifest.id)
            reason = latest.get("reason") if isinstance(latest, dict) else None
            extensions.append(
                {
                    "extension_id": manifest.id,
                    "name": manifest.name,
                    "state": st.value,
                    "status": (
                        f"error/{reason}"
                        if st == ExtensionState.ERROR and reason
                        else st.value
                    ),
                    "entrypoint": manifest.entrypoint,
                    "depends_on": list(manifest.depends_on),
                    "latest_diagnostic": latest,
                }
            )
        return {"counts": counts, "extensions": extensions}
