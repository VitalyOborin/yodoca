"""Embedding generation for memory content. Uses OpenAI text-embedding-3-large with 256-dim Matryoshka."""

import logging
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Thin wrapper around OpenAI embeddings. Returns None on error for graceful degradation."""

    MODEL = "text-embedding-3-large"
    DIMENSIONS = 256  # Matryoshka: ~95% quality at 1/12 size (ADR 005)

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)

    async def generate(self, content: str) -> list[float] | None:
        """Generate 256-dim embedding. Returns None on error (don't block fact save or search)."""
        if not content or not content.strip():
            return None
        try:
            resp = await self._client.embeddings.create(
                model=self.MODEL,
                input=content.strip(),
                dimensions=self.DIMENSIONS,
            )
            return list(resp.data[0].embedding)
        except Exception as e:
            logger.warning("Embedding generation failed: %s", e)
            return None
