"""Capability protocols for provider-agnostic features (embeddings, etc.)."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingCapability(Protocol):
    """Abstract client for embedding generation. Implemented by provider adapters."""

    async def embed_batch(
        self,
        texts: list[str],
        model: str,
        dimensions: int | None = None,
    ) -> list[list[float] | None]:
        """Generate embeddings for a list of texts.

        Returns a list parallel to texts: each element is an embedding vector
        or None if that specific text was empty or failed.
        """
        ...
