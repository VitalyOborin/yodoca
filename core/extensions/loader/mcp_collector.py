"""Collect MCP server instances and aliases from extensions (duck-typed hooks)."""

import logging
from typing import Any

from core.extensions.contract import ExtensionState

logger = logging.getLogger(__name__)


class McpCollector:
    """Reads ACTIVE extensions for optional get_mcp_servers / get_mcp_server_aliases."""

    def __init__(
        self,
        extensions: dict[str, Any],
        state: dict[str, ExtensionState],
    ) -> None:
        self._extensions = extensions
        self._state = state

    def get_mcp_servers(self) -> list[Any]:
        servers: list[Any] = []
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.ACTIVE:
                continue
            if not hasattr(ext, "get_mcp_servers") or not callable(ext.get_mcp_servers):
                continue
            try:
                result = ext.get_mcp_servers()
                if result:
                    servers.extend(result if isinstance(result, list) else list(result))
            except Exception as e:
                logger.exception("get_mcp_servers failed for %s: %s", ext_id, e)
        return servers

    def collect_mcp_aliases(self) -> list[str]:
        mcp_aliases: list[str] = []
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) != ExtensionState.ACTIVE:
                continue
            if not hasattr(ext, "get_mcp_server_aliases") or not callable(
                ext.get_mcp_server_aliases
            ):
                continue
            try:
                aliases = ext.get_mcp_server_aliases()
                if aliases:
                    mcp_aliases.extend(
                        aliases if isinstance(aliases, list) else list(aliases)
                    )
            except Exception as e:
                logger.exception("get_mcp_server_aliases failed for %s: %s", ext_id, e)
        return mcp_aliases
