"""DuckDuckGo search provider using ddgs library."""

import asyncio
import logging

from ddgs import DDGS

from interfaces import SearchResult, domain_matches

logger = logging.getLogger(__name__)

_TIME_RANGE_MAP = {
    "d": "d",
    "w": "w",
    "m": "m",
    "y": "y",
    "all": None,
}


class DuckDuckGoSearchProvider:
    """SearchProvider implementation using DuckDuckGo (ddgs library)."""

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: list[str] | None = None,
        time_range: str = "all",
    ) -> list[SearchResult]:
        """Search DuckDuckGo for the given query."""
        timelimit = _TIME_RANGE_MAP.get(time_range.lower(), None)
        try:
            raw_results = await asyncio.to_thread(
                _search_sync,
                query,
                max_results=limit * 2 if allowed_domains else limit,
                timelimit=timelimit,
            )
        except Exception as e:
            logger.warning("DuckDuckGo search failed: %s", e)
            return []

        results: list[SearchResult] = []
        for item in raw_results:
            url = item.get("href") or ""
            if allowed_domains and not domain_matches(url, allowed_domains):
                continue
            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=url,
                    snippet=item.get("body") or "",
                )
            )
            if len(results) >= limit:
                break

        return results


def _search_sync(
    query: str,
    *,
    max_results: int = 10,
    timelimit: str | None = None,
) -> list[dict]:
    """Synchronous search wrapper for asyncio.to_thread."""
    return list(
        DDGS().text(
            query,
            max_results=max_results,
            timelimit=timelimit,
        )
    )
