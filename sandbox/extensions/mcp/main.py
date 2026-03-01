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


async def _resolve_all_secrets(cfg: dict[str, Any], get_secret: Any) -> dict[str, Any] | None:
    """Resolve ${NAME} in url, headers values, env values. Return None if any missing."""
    resolved: dict[str, Any] = dict(cfg)

    if "url" in resolved and isinstance(resolved["url"], str):
        url = await _resolve_secrets_in_string(resolved["url"], get_secret)
        if url is None:
            return None
        resolved["url"] = url

    if "headers" in resolved and isinstance(resolved["headers"], dict):
        new_headers: dict[str, str] = {}
        for k, v in resolved["headers"].items():
            if isinstance(v, str):
                v2 = await _resolve_secrets_in_string(v, get_secret)
                if v2 is None:
                    return None
                new_headers[k] = v2
            else:
                new_headers[k] = v
        resolved["headers"] = new_headers

    if "env" in resolved and isinstance(resolved["env"], dict):
        new_env: dict[str, str] = {}
        for k, v in resolved["env"].items():
            if isinstance(v, str):
                v2 = await _resolve_secrets_in_string(v, get_secret)
                if v2 is None:
                    return None
                new_env[k] = v2
            else:
                new_env[k] = v
        resolved["env"] = new_env

    return resolved


def _extract_text_from_prompt_messages(messages: list[Any]) -> str:
    """Extract text from GetPromptResult.messages (TextContent)."""
    parts: list[str] = []
    for msg in messages or []:
        content = getattr(msg, "content", None)
        if content is not None and hasattr(content, "text") and content.text:
            parts.append(content.text)
    return "\n\n".join(parts) if parts else ""


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

    async def start(self) -> None:
        servers_cfg = self._ctx.get_config("servers") or []
        if not isinstance(servers_cfg, list):
            logger.warning("mcp: config.servers must be a list, got %s", type(servers_cfg))
            return

        servers: list[Any] = []
        self._active_aliases = []

        for entry in servers_cfg:
            if not isinstance(entry, dict):
                continue
            alias = entry.get("alias")
            transport = entry.get("transport")
            if not alias or not transport:
                logger.warning("mcp: server entry missing alias or transport, skip")
                continue

            resolved = await _resolve_all_secrets(entry, self._ctx.get_secret)
            if resolved is None:
                parts = [str(entry.get("url", ""))]
                parts.extend(str(v) for v in (entry.get("headers") or {}).values())
                parts.extend(str(v) for v in (entry.get("env") or {}).values())
                secret_names = list(dict.fromkeys(_SECRET_PATTERN.findall(" ".join(parts))))
                logger.warning(
                    "mcp: skipping server %s: missing secret(s) %s",
                    alias,
                    secret_names,
                )
                continue
            entry = resolved

            try:
                cache_tools = entry.get("cache_tools", False)
                tool_filter_val = None
                if "tool_filter" in entry:
                    names = entry["tool_filter"]
                    if isinstance(names, list) and names:
                        tool_filter_val = create_static_tool_filter(allowed_tool_names=names)
                require_approval_val = None
                if "require_approval" in entry:
                    rap = entry["require_approval"]
                    if isinstance(rap, dict) and "always" in rap:
                        tool_names = rap["always"]
                        if isinstance(tool_names, list) and tool_names:
                            require_approval_val = {"always": {"tool_names": tool_names}}

                if transport == "stdio":
                    command = entry.get("command")
                    args = entry.get("args")
                    if not command or not args:
                        logger.warning("mcp: server %s (stdio) missing command or args", alias)
                        continue
                    params: dict[str, Any] = {"command": command, "args": args}
                    if "env" in entry:
                        params["env"] = entry["env"]
                    server = MCPServerStdio(
                        name=alias,
                        params=params,
                        cache_tools_list=cache_tools,
                        tool_filter=tool_filter_val,
                        require_approval=require_approval_val,
                    )
                elif transport == "streamable-http":
                    url = entry.get("url")
                    if not url:
                        logger.warning("mcp: server %s (streamable-http) missing url", alias)
                        continue
                    params = {"url": url, "timeout": 10}
                    if "headers" in entry:
                        params["headers"] = entry["headers"]
                    server = MCPServerStreamableHttp(
                        name=alias,
                        params=params,
                        cache_tools_list=cache_tools,
                        tool_filter=tool_filter_val,
                        require_approval=require_approval_val,
                    )
                else:
                    logger.warning("mcp: server %s: unsupported transport %s", alias, transport)
                    continue
            except Exception as e:
                logger.exception("mcp: failed to build server %s: %s", alias, e)
                continue

            servers.append(server)
            self._active_aliases.append(alias)
            prompts_cfg = entry.get("prompts")
            if prompts_cfg is not None:
                self._server_prompts_config[alias] = prompts_cfg

        if not servers:
            return

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
        return [getattr(s, "name", "") for s in self._manager.active_servers if hasattr(s, "name")]

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
            if prompts_cfg is None or prompts_cfg == []:
                continue

            to_fetch: list[tuple[str, dict[str, Any] | None]] = []

            if prompts_cfg == "auto":
                try:
                    list_result = await server.list_prompts()
                    for p in getattr(list_result, "prompts", []) or []:
                        name = getattr(p, "name", "")
                        if name:
                            to_fetch.append((name, None))
                except Exception as e:
                    logger.debug("mcp: list_prompts failed for %s: %s", alias, e)
                    continue
            elif isinstance(prompts_cfg, list):
                for item in prompts_cfg:
                    if isinstance(item, dict):
                        name = item.get("name")
                        args = item.get("args")
                        if name:
                            to_fetch.append((name, args if isinstance(args, dict) else None))
                    elif isinstance(item, str):
                        to_fetch.append((item, None))

            for name, args in to_fetch:
                cache_key = (alias, name)
                cached = self._prompts_cache.get(cache_key)
                if cached is not None and (now - cached[1]) < cache_ttl:
                    text = cached[0]
                else:
                    try:
                        result = await server.get_prompt(name, args)
                        text = _extract_text_from_prompt_messages(
                            getattr(result, "messages", []) or []
                        )
                        self._prompts_cache[cache_key] = (text, now)
                    except Exception as e:
                        logger.debug("mcp: get_prompt %s/%s failed: %s", alias, name, e)
                        continue
                if text:
                    parts.append(f"[MCP Prompt: {alias}/{name}]\n{text}")

        if not parts:
            return None
        return "\n\n---\n\n".join(parts)

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
                        logger.info(
                            "mcp: reconnected %d server(s)",
                            len(reconnected),
                        )
                        await self._ctx.emit(
                            "mcp.servers_reconnected",
                            {
                                "reconnected": [
                                    getattr(s, "name", "")
                                    for s in reconnected
                                    if hasattr(s, "name")
                                ],
                            },
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
