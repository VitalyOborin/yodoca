"""Tavily search and read provider (hybrid: SearchProvider + ReadProvider)."""

import logging

from tavily import AsyncTavilyClient

from interfaces import ReadResult, SearchResult

logger = logging.getLogger(__name__)

_TIME_RANGE_MAP = {
    "d": "day",
    "w": "week",
    "m": "month",
    "y": "year",
    "all": None,
}


class TavilyProvider:
    """Hybrid provider: implements both SearchProvider and ReadProvider via Tavily API."""

    def __init__(self, api_key: str, max_page_length: int = 15000) -> None:
        self._client = AsyncTavilyClient(api_key=api_key)
        self._max_page_length = max_page_length

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
        time_range: str = "all",
    ) -> list[SearchResult]:
        """Search Tavily for the given query."""
        time_range_val = _TIME_RANGE_MAP.get(time_range.lower(), None)
        include_domains = allowed_domains if allowed_domains else None
        try:
            response = await self._client.search(
                query=query,
                max_results=limit,
                include_domains=include_domains,
                time_range=time_range_val,
            )
        except Exception as e:
            logger.warning("Tavily search failed: %s", e)
            return []

        results_raw = response.get("results") or []
        results: list[SearchResult] = []
        for item in results_raw:
            content = item.get("content") or ""
            snippet = content[:300] if len(content) > 300 else content
            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    snippet=snippet,
                )
            )
        return results

    async def read_url(self, url: str) -> ReadResult:
        """Extract content from the given URL via Tavily Extract API."""
        try:
            response = await self._client.extract(
                urls=[url],
                format="markdown",
            )
        except Exception as e:
            logger.warning("Tavily extract failed for %s: %s", url, e)
            return ReadResult(
                url=url,
                title="",
                content="",
                content_length=0,
                original_length=None,
                truncated=False,
                success=False,
                error=str(e),
            )

        failed = response.get("failed_results") or []
        for f in failed:
            if f.get("url") == url:
                err = f.get("error", "Unknown error")
                return ReadResult(
                    url=url,
                    title="",
                    content="",
                    content_length=0,
                    original_length=None,
                    truncated=False,
                    success=False,
                    error=err,
                )

        results_list = response.get("results") or []
        if not results_list:
            return ReadResult(
                url=url,
                title="",
                content="",
                content_length=0,
                original_length=None,
                truncated=False,
                success=False,
                error="No content extracted",
            )

        item = results_list[0]
        raw_content = item.get("raw_content") or ""
        title = _extract_title_from_markdown(raw_content) or url

        original_len = len(raw_content)
        if original_len > self._max_page_length:
            content = raw_content[: self._max_page_length]
            truncated = True
            content_length = self._max_page_length
        else:
            content = raw_content
            truncated = False
            content_length = original_len

        return ReadResult(
            url=url,
            title=title,
            content=content,
            content_length=content_length,
            original_length=original_len,
            truncated=truncated,
            success=True,
            error="",
        )


def _extract_title_from_markdown(text: str) -> str | None:
    """Extract first # heading or first non-empty line as title."""
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            return line.lstrip("#").strip()
        return line
    return None
