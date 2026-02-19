"""Virtual ToolProvider exposing core tools for declarative agents."""

from typing import Any

from core.tools import apply_patch_tool, file, request_restart, shell_tool


class CoreToolsProvider:
    """Exposes core tools for declarative agents.

    Pass model_router + agent_id so hosted-only tools (WebSearchTool, ShellTool)
    are omitted when the agent's provider does not support them.
    """

    def __init__(self, model_router: Any = None, agent_id: str | None = None) -> None:
        self._model_router = model_router
        self._agent_id = agent_id

    def get_tools(self) -> list[Any]:
        tools: list[Any] = [file, apply_patch_tool, request_restart]
        if self._supports_hosted():
            from agents import WebSearchTool
            tools.extend([shell_tool, WebSearchTool()])
        return tools

    def _supports_hosted(self) -> bool:
        if self._model_router is None or self._agent_id is None:
            return True
        return self._model_router.supports_hosted_tools(self._agent_id)
