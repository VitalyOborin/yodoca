"""Embedding extension: provider-agnostic embedding generation via ModelRouter."""

import logging
from typing import Any

from core.llm.capabilities import EmbeddingCapability

logger = logging.getLogger(__name__)


class EmbeddingExtension:
    """Embedding generation via configured LLM provider."""

    def __init__(self) -> None:
        self._embedder: EmbeddingCapability | None = None
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
        if not self._embedder or not text or not text.strip():
            return None
        results = await self._embedder.embed_batch(
            [text],
            model=model or self._default_model,
            dimensions=dimensions or self._default_dimensions,
        )
        return results[0] if results else None

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
        """
        if not self._embedder or not texts:
            return [None] * len(texts)
        return await self._embedder.embed_batch(
            texts,
            model=model or self._default_model,
            dimensions=dimensions or self._default_dimensions,
        )

    # --- Lifecycle ---

    async def initialize(self, context: Any) -> None:
        self._default_model = context.get_config(
            "default_model", "text-embedding-3-large"
        )
        self._default_dimensions = context.get_config("default_dimensions", 256)
        provider_id = context.get_config("provider")
        router = context.model_router
        if router:
            self._embedder = router.get_capability(EmbeddingCapability, provider_id)
        if not self._embedder:
            logger.warning(
                "No embedding-capable provider found, embedding disabled"
            )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._embedder = None

    def health_check(self) -> bool:
        return self._embedder is not None
