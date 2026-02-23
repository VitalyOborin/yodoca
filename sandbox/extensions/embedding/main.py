"""Embedding extension: provider-agnostic embedding generation via ModelRouter."""

import logging
import os
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class EmbeddingExtension:
    """Embedding generation via configured LLM provider."""

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        self._default_model: str = "text-embedding-3-large"
        self._default_dimensions: int = 256

    async def embed(
        self,
        text: str,
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> list[float] | None:
        """Generate embedding. Returns None on error (graceful degradation)."""
        if not self._client or not text or not text.strip():
            return None
        try:
            resp = await self._client.embeddings.create(
                model=model or self._default_model,
                input=text.strip(),
                dimensions=dimensions or self._default_dimensions,
            )
            return list(resp.data[0].embedding)
        except Exception as e:
            logger.warning("Embedding failed: %s", e)
            return None

    async def embed_batch(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> list[list[float] | None]:
        """Batch-embed multiple texts in a single API call.

        Returns a list parallel to texts: each element is an embedding vector or
        None if that specific text was empty / failed.
        Falls back to sequential embed() calls if the batch API fails.
        """
        if not self._client or not texts:
            return [None] * len(texts)
        cleaned = [t.strip() if t else "" for t in texts]
        non_empty = [(i, t) for i, t in enumerate(cleaned) if t]
        if not non_empty:
            return [None] * len(texts)
        try:
            resp = await self._client.embeddings.create(
                model=model or self._default_model,
                input=[t for _, t in non_empty],
                dimensions=dimensions or self._default_dimensions,
            )
            result: list[list[float] | None] = [None] * len(texts)
            for emb_data, (orig_idx, _) in zip(resp.data, non_empty):
                result[orig_idx] = list(emb_data.embedding)
            return result
        except Exception as e:
            logger.warning(
                "Batch embedding failed, falling back to sequential: %s", e
            )
            return [
                await self.embed(t, model=model, dimensions=dimensions)
                for t in texts
            ]

    # --- Lifecycle ---

    async def initialize(self, context: Any) -> None:
        self._default_model = context.get_config(
            "default_model", "text-embedding-3-large"
        )
        self._default_dimensions = context.get_config("default_dimensions", 256)
        provider_id = context.get_config("provider")
        self._client = self._build_client(context, provider_id)
        if not self._client:
            logger.warning(
                "No embedding-capable provider found, embedding disabled"
            )

    def _build_client(self, context: Any, provider_id: str | None) -> AsyncOpenAI | None:
        router = context.model_router
        if router:
            return router.get_provider_client(provider_id)
        key = os.environ.get("OPENAI_API_KEY")
        return AsyncOpenAI(api_key=key) if key else None

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._client = None

    def health_check(self) -> bool:
        return self._client is not None
