"""SearXNG search provider (self-hosted meta-search)."""

import logging
from urllib.parse import urljoin

import httpx

from interfaces import SearchResult, domain_matches

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0

_TIME_RANGE_MAP = {
    "d": "day",
    "w": "week",
    "m": "month",
    "y": "year",
    "all": None,
}


class SearXngSearchProvider:
    """SearchProvider implementation using SearXNG JSON API (self-hosted)."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
        time_range: str = "all",
    ) -> list[SearchResult]:
        """Search SearXNG for the given query."""
        search_url = urljoin(self._base_url + "/", "search")
        params: dict[str, str | int] = {
            "q": query,
            "format": "json",
            "pageno": 1,
            "safesearch": 0,
        }
        time_val = _TIME_RANGE_MAP.get(time_range.lower(), None)
        if time_val is not None:
            params["time_range"] = time_val

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(search_url, params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.warning("SearXNG search failed: %s", e)
            return []

        raw_results = data.get("results") or []
        results: list[SearchResult] = []
        for item in raw_results:
            url = item.get("url") or ""
            if allowed_domains and not domain_matches(url, allowed_domains):
                continue
            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=url,
                    snippet=item.get("content") or "",
                )
            )
            if len(results) >= limit:
                break

        return results
