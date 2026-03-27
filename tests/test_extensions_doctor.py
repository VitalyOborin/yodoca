"""Tests for extensions_doctor tooling."""

import pytest

from core.tools.extensions_doctor import make_extensions_doctor_tool


@pytest.mark.asyncio
async def test_extensions_doctor_returns_structured_report() -> None:
    tool = make_extensions_doctor_tool(
        lambda: {
            "counts": {"active": 1, "inactive": 0, "error": 1},
            "extensions": [
                {
                    "extension_id": "ok",
                    "name": "Ok",
                    "state": "active",
                    "status": "active",
                    "entrypoint": "main:Ok",
                    "depends_on": [],
                    "latest_diagnostic": None,
                },
                {
                    "extension_id": "broken",
                    "name": "Broken",
                    "state": "error",
                    "status": "error/import_error",
                    "entrypoint": "main:Broken",
                    "depends_on": [],
                    "latest_diagnostic": {
                        "extension_id": "broken",
                        "phase": "load",
                        "reason": "import_error",
                        "message": "No module named x",
                        "exception_type": "ModuleNotFoundError",
                        "traceback": "traceback",
                        "dependency_chain": [],
                        "created_at": "2026-03-27T00:00:00+00:00",
                    },
                },
            ],
        }
    )

    result = await tool.on_invoke_tool(None, "{}")

    assert result.success is True
    assert result.counts == {"active": 1, "inactive": 0, "error": 1}
    assert result.extensions[1].latest_diagnostic is not None
    assert result.extensions[1].latest_diagnostic.reason == "import_error"
