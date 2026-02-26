"""Jina Reader provider for URL content extraction."""

import logging

import httpx

from interfaces import ReadResult

logger = logging.getLogger(__name__)

JINA_BASE = "https://r.jina.ai/"
DEFAULT_TIMEOUT = 30.0


class JinaReadProvider:
    """ReadProvider implementation using Jina Reader API (r.jina.ai)."""

    def __init__(self, max_page_length: int = 15000) -> None:
        self._max_page_length = max_page_length

    async def read_url(self, url: str) -> ReadResult:
        """Fetch and extract content from the given URL via Jina Reader."""
        jina_url = JINA_BASE + url
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.get(
                    jina_url,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data", payload)
        except httpx.HTTPStatusError as e:
            logger.warning("Jina Reader HTTP error for %s: %s", url, e)
            return ReadResult(
                url=url,
                title="",
                content="",
                content_length=0,
                original_length=None,
                truncated=False,
                success=False,
                error=f"HTTP {e.response.status_code}: {str(e)}",
            )
        except Exception as e:
            logger.warning("Jina Reader failed for %s: %s", url, e)
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

        title = data.get("title") or ""
        content = data.get("content") or ""
        original_len = len(content)

        if len(content) > self._max_page_length:
            content = content[: self._max_page_length]
            truncated = True
            content_length = self._max_page_length
        else:
            truncated = False
            content_length = len(content)

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
