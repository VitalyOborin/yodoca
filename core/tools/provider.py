"""Virtual ToolProvider exposing core tools for declarative agents."""

from typing import Any

from agents import WebSearchTool

from core.tools import apply_patch_tool, file, request_restart, shell_tool


class CoreToolsProvider:
    """ToolProvider that exposes shell, file, patch, restart, and web_search tools."""

    def get_tools(self) -> list[Any]:
        return [
            shell_tool,
            file,
            apply_patch_tool,
            request_restart,
            WebSearchTool(),
        ]
