"""MCP Bridge Extension: connects to MCP servers from config and exposes tools to the Orchestrator via mcp_servers."""

import asyncio
import re
import logging
import time
from typing import Any

from agents.mcp import (
    MCPServerManager,
    MCPServerStdio,
    MCPServerStreamableHttp,
    create_static_tool_filter,
)

logger = logging.getLogger(__name__)

_SECRET_PATTERN = re.compile(r"\$\{(\w+)\}")


async def _resolve_secrets_in_string(value: str, get_secret: Any) -> str | None:
    """Replace ${NAME} with secret. Return None if any secret is missing."""
    result_parts: list[str] = []
    last_end = 0
    for match in _SECRET_PATTERN.finditer(value):
        name = match.group(1)
        secret = await get_secret(name)
        if secret is None:
            return None
        result_parts.append(value[last_end : match.start()])
        result_parts.append(secret)
        last_end = match.end()
    result_parts.append(value[last_end:])
    return "".join(result_parts)


async def _resolve_dict_secrets(
    d: dict[str, Any], get_secret: Any
) -> dict[str, Any] | None:
    """Resolve ${NAME} in all string values of a dict. Return None if any secret is missing."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str):
            resolved_v = await _resolve_secrets_in_string(v, get_secret)
            if resolved_v is None:
                return None
            out[k] = resolved_v
        else:
            out[k] = v
    return out


async def _resolve_all_secrets(cfg: dict[str, Any], get_secret: Any) -> dict[str, Any] | None:
    """Resolve ${NAME} in url, headers values, env values. Return None if any missing."""
    resolved: dict[str, Any] = dict(cfg)

    if "url" in resolved and isinstance(resolved["url"], str):
        url = await _resolve_secrets_in_string(resolved["url"], get_secret)
        if url is None:
            return None
        resolved["url"] = url

    for key in ("headers", "env"):
        if key in resolved and isinstance(resolved[key], dict):
            result = await _resolve_dict_secrets(resolved[key], get_secret)
            if result is None:
                return None
            resolved[key] = result

    return resolved


def _extract_text_from_prompt_messages(messages: list[Any]) -> str:
    """Extract text from GetPromptResult.messages (TextContent)."""
    parts: list[str] = []
    for msg in messages or []:
        content = getattr(msg, "content", None)
        if content is not None and hasattr(content, "text") and content.text:
            parts.append(content.text)
    return "\n\n".join(parts) if parts else ""


def _parse_common_options(entry: dict[str, Any]) -> dict[str, Any]:
    """Extract cache_tools, tool_filter, require_approval from a server config entry."""
    opts: dict[str, Any] = {"cache_tools_list": entry.get("cache_tools", False)}

    names = entry.get("tool_filter")
    if isinstance(names, list) and names:
        opts["tool_filter"] = create_static_tool_filter(allowed_tool_names=names)

    rap = entry.get("require_approval")
    if isinstance(rap, dict):
        tool_names = rap.get("always")
        if isinstance(tool_names, list) and tool_names:
            opts["require_approval"] = {"always": {"tool_names": tool_names}}

    return opts


def _build_server(alias: str, entry: dict[str, Any]) -> Any | None:
    """Build MCPServerStdio or MCPServerStreamableHttp from config. Returns None on bad config."""
    transport = entry.get("transport")
    common = _parse_common_options(entry)

    if transport == "stdio":
        command = entry.get("command")
        args = entry.get("args")
        if not command or not args:
            logger.warning("mcp: server %s (stdio) missing command or args", alias)
            return None
        params: dict[str, Any] = {"command": command, "args": args}
        if "env" in entry:
            params["env"] = entry["env"]
        return MCPServerStdio(name=alias, params=params, **common)

    if transport == "streamable-http":
        url = entry.get("url")
        if not url:
            logger.warning("mcp: server %s (streamable-http) missing url", alias)
            return None
        params = {"url": url, "timeout": 10}
        if "headers" in entry:
            params["headers"] = entry["headers"]
        return MCPServerStreamableHttp(name=alias, params=params, **common)

    logger.warning("mcp: server %s: unsupported transport %s", alias, transport)
    return None


def _find_missing_secret_names(entry: dict[str, Any]) -> list[str]:
    """Extract ${SECRET} names from url, headers, env values for diagnostics."""
    parts = [str(entry.get("url", ""))]
    parts.extend(str(v) for v in (entry.get("headers") or {}).values())
    parts.extend(str(v) for v in (entry.get("env") or {}).values())
    return list(dict.fromkeys(_SECRET_PATTERN.findall(" ".join(parts))))


class McpBridgeExtension:
    """Extension + ServiceProvider + ContextProvider: manages MCP server lifecycle and exposes active_servers."""

    def __init__(self) -> None:
        self._ctx: Any = None
        self._manager: MCPServerManager | None = None
        self._active_aliases: list[str] = []
        self._server_prompts_config: dict[str, list[dict[str, Any]] | str] = {}
        self._prompts_cache: dict[tuple[str, str], tuple[str, float]] = {}

    async def initialize(self, context: Any) -> None:
        self._ctx = context

    async def _resolve_and_build_server(
        self, entry: dict[str, Any], alias: str
    ) -> tuple[Any | None, dict[str, Any] | None]:
        """Resolve secrets and build one server. Returns (server, resolved_entry) or (None, None)."""
        resolved = await _resolve_all_secrets(entry, self._ctx.get_secret)
        if resolved is None:
            logger.warning(
                "mcp: skipping server %s: missing secret(s) %s",
                alias,
                _find_missing_secret_names(entry),
            )
            return (None, None)
        try:
            server = _build_server(alias, resolved)
        except Exception as e:
            logger.exception("mcp: failed to build server %s: %s", alias, e)
            return (None, None)
        if server is None:
            return (None, None)
        return (server, resolved)

    async def _build_servers_from_config(
        self,
    ) -> tuple[list[Any], list[str], dict[str, list[dict[str, Any]] | str]]:
        """Build server list and prompts config from config.servers. Returns (servers, aliases, prompts_config)."""
        servers_cfg = self._ctx.get_config("servers") or []
        if not isinstance(servers_cfg, list):
            logger.warning("mcp: config.servers must be a list, got %s", type(servers_cfg))
            return ([], [], {})

        servers: list[Any] = []
        active_aliases: list[str] = []
        server_prompts_config: dict[str, list[dict[str, Any]] | str] = {}

        for entry in servers_cfg:
            if not isinstance(entry, dict):
                continue
            alias = entry.get("alias")
            transport = entry.get("transport")
            if not alias or not transport:
                logger.warning("mcp: server entry missing alias or transport, skip")
                continue
            server, resolved = await self._resolve_and_build_server(entry, alias)
            if server is None:
                continue
            servers.append(server)
            active_aliases.append(alias)
            prompts_cfg = resolved.get("prompts") if resolved else None
            if prompts_cfg is not None:
                server_prompts_config[alias] = prompts_cfg

        return (servers, active_aliases, server_prompts_config)

    async def start(self) -> None:
        servers, active_aliases, server_prompts_config = await self._build_servers_from_config()
        if not servers:
            return
        self._active_aliases = active_aliases
        self._server_prompts_config = server_prompts_config
        self._manager = MCPServerManager(
            servers,
            drop_failed_servers=True,
            connect_timeout_seconds=10,
        )
        await self._manager.__aenter__()
        if self._manager.failed_servers:
            logger.warning(
                "mcp: %d server(s) failed to connect: %s",
                len(self._manager.failed_servers),
                [getattr(s, "name", "?") for s in self._manager.failed_servers],
            )

    def get_mcp_servers(self) -> list[Any]:
        if not self._manager:
            return []
        return list(self._manager.active_servers)

    def get_mcp_server_aliases(self) -> list[str]:
        if not self._manager:
            return []
        return [getattr(s, "name", "") for s in self._manager.active_servers]

    @property
    def context_priority(self) -> int:
        return 80

    async def get_context(self, prompt: str, turn_context: Any) -> str | None:
        """Fetch MCP prompts from configured servers and return combined context."""
        if not self._manager or not self._manager.active_servers:
            return None
        cache_ttl = self._ctx.get_config("prompts_cache_ttl", 300)
        now = time.time()
        parts: list[str] = []

        for server in self._manager.active_servers:
            alias = getattr(server, "name", "")
            if not alias:
                continue
            prompts_cfg = self._server_prompts_config.get(alias)
            if not prompts_cfg:
                continue

            to_fetch = await self._resolve_prompts_to_fetch(server, alias, prompts_cfg)

            for name, args in to_fetch:
                text = await self._fetch_prompt_cached(
                    server, alias, name, args, now, cache_ttl
                )
                if text:
                    parts.append(f"[MCP Prompt: {alias}/{name}]\n{text}")

        if not parts:
            return None
        return "\n\n---\n\n".join(parts)

    async def _resolve_prompts_to_fetch(
        self, server: Any, alias: str, prompts_cfg: Any
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Turn prompts config ('auto' or explicit list) into (name, args) pairs."""
        if prompts_cfg == "auto":
            try:
                list_result = await server.list_prompts()
                return [
                    (getattr(p, "name", ""), None)
                    for p in getattr(list_result, "prompts", []) or []
                    if getattr(p, "name", "")
                ]
            except Exception as e:
                logger.debug("mcp: list_prompts failed for %s: %s", alias, e)
                return []

        result: list[tuple[str, dict[str, Any] | None]] = []
        if isinstance(prompts_cfg, list):
            for item in prompts_cfg:
                if isinstance(item, dict):
                    name = item.get("name")
                    args = item.get("args")
                    if name:
                        result.append((name, args if isinstance(args, dict) else None))
                elif isinstance(item, str):
                    result.append((item, None))
        return result

    async def _fetch_prompt_cached(
        self,
        server: Any,
        alias: str,
        name: str,
        args: dict[str, Any] | None,
        now: float,
        cache_ttl: float,
    ) -> str:
        """Return prompt text from cache or fetch from server. Empty string on failure."""
        cache_key = (alias, name)
        cached = self._prompts_cache.get(cache_key)
        if cached is not None and (now - cached[1]) < cache_ttl:
            return cached[0]
        try:
            result = await server.get_prompt(name, args)
            text = _extract_text_from_prompt_messages(
                getattr(result, "messages", []) or []
            )
            self._prompts_cache[cache_key] = (text, now)
            return text
        except Exception as e:
            logger.debug("mcp: get_prompt %s/%s failed: %s", alias, name, e)
            return ""

    async def run_background(self) -> None:
        interval = self._ctx.get_config("reconnect_interval", 120)
        try:
            while True:
                await asyncio.sleep(interval)
                if not self._manager:
                    continue
                if self._manager.failed_servers:
                    reconnected = await self._manager.reconnect(failed_only=True)
                    if reconnected:
                        names = [getattr(s, "name", "") for s in reconnected]
                        logger.info("mcp: reconnected %d server(s)", len(reconnected))
                        await self._ctx.emit(
                            "mcp.servers_reconnected",
                            {"reconnected": names},
                        )
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._manager:
            await self._manager.__aexit__(None, None, None)
            self._manager = None

    async def destroy(self) -> None:
        self._manager = None
        self._active_aliases = []
        self._server_prompts_config = {}
        self._prompts_cache = {}

    def health_check(self) -> bool:
        if not self._manager:
            return len(self._ctx.get_config("servers") or []) == 0
        return len(self._manager.active_servers) > 0
