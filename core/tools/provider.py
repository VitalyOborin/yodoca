"""Virtual ToolProvider exposing core tools for declarative agents."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.llm import ModelRouterProtocol
from core.tools import (
    apply_patch_tool,
    file,
    make_extensions_doctor_tool,
    make_restart_tool,
)


class CoreToolsProvider:
    """Exposes core tools for declarative agents.

    Pass restart_file_path for the request_restart tool.
    """

    def __init__(
        self,
        model_router: ModelRouterProtocol | None = None,
        agent_id: str | None = None,
        restart_file_path: Path | None = None,
        extension_report_getter: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._model_router = model_router
        self._agent_id = agent_id
        self._restart_file_path = restart_file_path
        self._extension_report_getter = extension_report_getter

    def get_tools(self) -> list[Any]:
        tools: list[Any] = [file, apply_patch_tool]
        if self._restart_file_path is not None:
            tools.append(make_restart_tool(self._restart_file_path))
        if self._extension_report_getter is not None:
            tools.append(make_extensions_doctor_tool(self._extension_report_getter))
        return tools
