"""ToolResolver: resolve tool IDs to concrete tool objects."""

from pathlib import Path
from typing import Any

from core.extensions.contract import ToolProvider
from core.llm import ModelRouterProtocol


class ToolResolver:
    """Resolves tools declared in manifest uses_tools."""

    def __init__(
        self,
        extensions: dict[str, Any],
        model_router: ModelRouterProtocol | None,
        restart_file_path: Path,
    ) -> None:
        self._extensions = extensions
        self._model_router = model_router
        self._restart_file_path = restart_file_path

    def resolve_tools(
        self, tool_ids: list[str], agent_id: str | None = None
    ) -> list[Any]:
        """Resolve extension IDs and core_tools into concrete tool callables."""
        tools: list[Any] = []
        for ext_id in tool_ids:
            if ext_id == "core_tools":
                from core.tools.provider import CoreToolsProvider

                tools.extend(
                    CoreToolsProvider(
                        model_router=self._model_router,
                        agent_id=agent_id,
                        restart_file_path=self._restart_file_path,
                    ).get_tools()
                )
                continue
            ext = self._extensions.get(ext_id)
            if ext and isinstance(ext, ToolProvider):
                tools.extend(ext.get_tools())
        return tools
