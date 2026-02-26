"""Virtual ToolProvider exposing core tools for declarative agents."""

from pathlib import Path
from typing import Any

from core.llm import ModelRouterProtocol
from core.tools import apply_patch_tool, file, make_restart_tool


class CoreToolsProvider:
    """Exposes core tools for declarative agents.

    Pass restart_file_path for the request_restart tool.
    """

    def __init__(
        self,
        model_router: ModelRouterProtocol | None = None,
        agent_id: str | None = None,
        restart_file_path: Path | None = None,
    ) -> None:
        self._model_router = model_router
        self._agent_id = agent_id
        self._restart_file_path = restart_file_path

    def get_tools(self) -> list[Any]:
        tools: list[Any] = [file, apply_patch_tool]
        if self._restart_file_path is not None:
            tools.append(make_restart_tool(self._restart_file_path))
        return tools
