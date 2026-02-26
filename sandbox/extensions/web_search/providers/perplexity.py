"""Perplexity Search API provider (SearchProvider)."""

import logging

import httpx

from interfaces import SearchResult

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.perplexity.ai/search"
DEFAULT_TIMEOUT = 30.0

_TIME_RANGE_MAP = {
    "d": "day",
    "w": "week",
    "m": "month",
    "y": "year",
    "all": None,
}


class PerplexitySearchProvider:
    """SearchProvider implementation using Perplexity Search API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
        time_range: str = "all",
    ) -> list[SearchResult]:
        """Search Perplexity for the given query."""
        payload: dict = {
            "query": query,
            "max_results": min(limit, 20),
        }
        recency = _TIME_RANGE_MAP.get(time_range.lower(), None)
        if recency is not None:
            payload["search_recency_filter"] = recency
        if allowed_domains:
            payload["search_domain_filter"] = allowed_domains

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    SEARCH_URL,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.warning("Perplexity search failed: %s", e)
            return []

        raw_results = data.get("results") or []
        return [
            SearchResult(
                title=item.get("title") or "",
                url=item.get("url") or "",
                snippet=item.get("snippet") or "",
            )
            for item in raw_results[:limit]
        ]
