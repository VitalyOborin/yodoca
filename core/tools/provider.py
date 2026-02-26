"""Virtual ToolProvider exposing core tools for declarative agents."""

from pathlib import Path
from typing import Any

from core.llm import ModelRouterProtocol
from core.tools import apply_patch_tool, file, make_restart_tool


class CoreToolsProvider:
    """Exposes core tools for declarative agents.

    Pass model_router + agent_id so hosted-only tools (WebSearchTool)
    are omitted when the agent's provider does not support them.
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
        if self._supports_hosted():
            from agents import WebSearchTool
            tools.extend([WebSearchTool()])
        return tools

    def _supports_hosted(self) -> bool:
        if self._model_router is None or self._agent_id is None:
            return True
        return self._model_router.supports_hosted_tools(self._agent_id)
