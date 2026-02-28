"""Protocols and data models for web search extension providers."""

from typing import Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel


def domain_matches(url: str, allowed_domains: list[str] | None) -> bool:
    """Return True if url's host is in allowed_domains, or if allowed_domains is empty/None."""
    if not allowed_domains:
        return True
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if not host:
            return False
        for domain in allowed_domains:
            d = domain.lower().strip()
            if not d:
                continue
            if d.startswith("."):
                if host == d[1:] or host.endswith(d):
                    return True
            elif host == d or host.endswith("." + d):
                return True
        return False
    except Exception:
        return False


# --- Provider layer models ---


class SearchResult(BaseModel):
    """Single search result from a SearchProvider."""

    title: str
    url: str
    snippet: str


class ReadResult(BaseModel):
    """Result of reading a URL via a ReadProvider."""

    url: str
    title: str
    content: str
    content_length: int
    original_length: int | None = None
    truncated: bool = False
    success: bool
    error: str = ""


# --- Provider protocols ---


class SearchProvider(Protocol):
    """Accepts a text query, returns ranked search results."""

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
        time_range: str = "all",
    ) -> list[SearchResult]:
        """Search the web for the given query."""
        ...


class ReadProvider(Protocol):
    """Accepts a URL, returns extracted page content as Markdown."""

    async def read_url(self, url: str) -> ReadResult:
        """Fetch and extract content from the given URL."""
        ...


# --- Tool output models ---


class SearchResultItem(BaseModel):
    """Single search result in tool output."""

    title: str
    url: str
    snippet: str


class WebSearchToolResult(BaseModel):
    """Structured result of web_search tool."""

    results: list[SearchResultItem]
    message: str = ""


class PageResult(BaseModel):
    """Result for a single page in a batch request."""

    url: str
    title: str
    content: str
    content_length: int
    original_length: int | None = None
    truncated: bool = False
    status: Literal["success", "error"]
    error: str = ""


class OpenPageToolResult(BaseModel):
    """Structured result of open_page tool (supports batch fetching)."""

    pages: list[PageResult]
    total: int
    success_count: int
    error_count: int
    total_content_length: int
    budget_warning: str = ""
