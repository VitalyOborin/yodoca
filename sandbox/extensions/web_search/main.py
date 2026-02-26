"""Web Search extension: ToolProvider for web search and page reading."""

import ipaddress
import logging
import socket
import sys
from pathlib import Path
from typing import Any

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from agents import function_tool
from urllib.parse import urlparse

from interfaces import (
    OpenPageToolResult,
    SearchResultItem,
    WebSearchToolResult,
)
from providers.duckduckgo import DuckDuckGoSearchProvider
from providers.jina import JinaReadProvider
from providers.searxng import SearXngSearchProvider
from providers.tavily import TavilyProvider

logger = logging.getLogger(__name__)


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL for SSRF: reject non-HTTP(S), private/loopback addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme: {parsed.scheme}"
    hostname = parsed.hostname
    if not hostname:
        return False, "Missing hostname"
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False, f"DNS resolution failed: {hostname}"
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
        ):
            return False, f"Blocked private/reserved address: {addr}"
    return True, ""


class WebSearchExtension:
    """Extension providing web_search and open_page tools."""

    def __init__(self) -> None:
        self._ctx: Any = None
        self._searcher: Any = None
        self._reader: Any = None
        self._tavily_instance: TavilyProvider | None = None
        self._max_page_length: int = 15000

    async def initialize(self, context: Any) -> None:
        self._ctx = context
        self._max_page_length = context.get_config("max_page_length", 15000)

        search_name = context.get_config("search_provider", "duckduckgo")
        read_name = context.get_config("read_provider", "jina")

        self._searcher = await self._create_search_provider(search_name)
        self._reader = await self._create_read_provider(read_name)

        logger.info(
            "WebSearch initialized: search=%s, read=%s",
            search_name,
            read_name,
        )

    async def _get_or_create_tavily(self) -> TavilyProvider:
        if self._tavily_instance is None:
            api_key = await self._ctx.get_secret("TAVILY_API_KEY")
            if not api_key:
                raise ValueError(
                    "TAVILY_API_KEY secret not set. Use configure_extension to provide it."
                )
            self._tavily_instance = TavilyProvider(
                api_key=api_key,
                max_page_length=self._max_page_length,
            )
        return self._tavily_instance

    async def _create_search_provider(self, name: str) -> Any:
        if name == "duckduckgo":
            return DuckDuckGoSearchProvider()
        if name == "tavily":
            return await self._get_or_create_tavily()
        if name == "searxng":
            base_url = self._ctx.get_config("searxng_base_url", "http://localhost:8080")
            return SearXngSearchProvider(base_url=base_url)
        raise ValueError(
            f"Unknown search_provider: {name}. Supports: duckduckgo, tavily, searxng"
        )

    async def _create_read_provider(self, name: str) -> Any:
        if name == "jina":
            return JinaReadProvider(max_page_length=self._max_page_length)
        if name == "tavily":
            return await self._get_or_create_tavily()
        raise ValueError(f"Unknown read_provider: {name}. Supports: jina, tavily")

    def get_setup_schema(self) -> list[dict]:
        """SetupProvider: schema for Tavily API key onboarding."""
        return [
            {
                "name": "tavily_api_key",
                "description": "Tavily API key (get one at tavily.com)",
                "secret": True,
                "required": False,
            },
        ]

    async def apply_config(self, name: str, value: str) -> None:
        """SetupProvider: save Tavily API key to keyring."""
        if not self._ctx:
            raise RuntimeError("Extension not initialized")
        if name == "tavily_api_key":
            await self._ctx.set_secret("TAVILY_API_KEY", (value or "").strip())

    async def on_setup_complete(self) -> tuple[bool, str]:
        """SetupProvider: verify Tavily API key when Tavily provider is used."""
        if not self._ctx:
            return False, "Extension not initialized"
        search_name = self._ctx.get_config("search_provider", "duckduckgo")
        read_name = self._ctx.get_config("read_provider", "jina")
        needs_tavily = search_name == "tavily" or read_name == "tavily"
        if not needs_tavily:
            return True, "No Tavily key needed for current providers."
        api_key = await self._ctx.get_secret("TAVILY_API_KEY")
        if not api_key:
            return False, "TAVILY_API_KEY is required when using Tavily provider."
        try:
            from tavily import AsyncTavilyClient

            client = AsyncTavilyClient(api_key=api_key)
            await client.search("test", max_results=1)
            return True, "Tavily API key is valid."
        except Exception as e:
            return False, f"Tavily API key validation failed: {e}"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return True

    def get_tools(self) -> list[Any]:
        return [
            function_tool(name_override="web_search")(self._tool_web_search),
            function_tool(name_override="open_page")(self._tool_open_page),
        ]

    async def _tool_web_search(
        self,
        query: str,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
        time_range: str = "all",
    ) -> WebSearchToolResult:
        """
        Search the web for current information. Returns a list of relevant URLs and snippets.
        Use open_page to read the full content of any URL if the snippet is not enough.

        Args:
            query: The search query.
            limit: Maximum number of results to return. Default: 5.
            allowed_domains: Optional list of domains to restrict results (e.g. ['wikipedia.org', 'github.com']).
            time_range: Filter by recency: d (day), w (week), m (month), y (year), all. Default: all.
        """
        if not self._searcher:
            return WebSearchToolResult(
                results=[],
                message="Extension not initialized.",
            )

        results = await self._searcher.search(
            query,
            limit=limit,
            allowed_domains=allowed_domains or [],
            time_range=time_range,
        )

        items = [
            SearchResultItem(title=r.title, url=r.url, snippet=r.snippet)
            for r in results
        ]

        message = (
            "Use open_page tool to read the full content of any URL if the snippet is not enough."
            if items
            else ""
        )

        return WebSearchToolResult(results=items, message=message)

    async def _tool_open_page(
        self,
        url: str,
        max_length: int | None = None,
    ) -> OpenPageToolResult:
        """
        Fetch and read the full text content of a specific webpage.

        Args:
            url: The URL of the webpage to open.
            max_length: Optional override for max content length (chars). Use to save context budget.
        """
        if not self._reader:
            return OpenPageToolResult(
                url=url,
                title="",
                content="",
                content_length=0,
                original_length=None,
                truncated=False,
                status="error",
                error="Extension not initialized.",
            )

        ok, err = _validate_url(url)
        if not ok:
            return OpenPageToolResult(
                url=url,
                title="",
                content="",
                content_length=0,
                original_length=None,
                truncated=False,
                status="error",
                error=err,
            )

        result = await self._reader.read_url(url)

        if not result.success:
            return OpenPageToolResult(
                url=result.url,
                title=result.title,
                content=result.content,
                content_length=result.content_length,
                original_length=result.original_length,
                truncated=result.truncated,
                status="error",
                error=result.error,
            )

        content = result.content
        content_length = result.content_length
        original_length = result.original_length
        truncated = result.truncated

        if max_length is not None and max_length < len(content):
            content = content[:max_length]
            content_length = max_length
            truncated = True
            if original_length is None:
                original_length = len(result.content)

        return OpenPageToolResult(
            url=result.url,
            title=result.title,
            content=content,
            content_length=content_length,
            original_length=original_length,
            truncated=truncated,
            status="success",
            error="",
        )
