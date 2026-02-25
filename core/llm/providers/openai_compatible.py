"""OpenAI and OpenAI-compatible providers (OpenAI, OpenRouter, etc.). Uses Responses API by default."""

import logging

from openai import AsyncOpenAI

from agents import OpenAIResponsesModel
from core.llm.protocol import ProviderConfig

logger = logging.getLogger(__name__)


class OpenAIEmbedder:
    """Adapter implementing EmbeddingCapability for OpenAI-compatible APIs."""

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def embed_batch(
        self,
        texts: list[str],
        model: str,
        dimensions: int | None = None,
    ) -> list[list[float] | None]:
        if not texts:
            return []
        cleaned = [t.strip() if t else "" for t in texts]
        non_empty = [(i, t) for i, t in enumerate(cleaned) if t]
        if not non_empty:
            return [None] * len(texts)
        try:
            kwargs: dict = {
                "model": model,
                "input": [t for _, t in non_empty],
            }
            if dimensions is not None:
                kwargs["dimensions"] = dimensions
            resp = await self._client.embeddings.create(**kwargs)
            result: list[list[float] | None] = [None] * len(texts)
            for emb_data, (orig_idx, _) in zip(resp.data, non_empty):
                result[orig_idx] = list(emb_data.embedding)
            return result
        except Exception as e:
            logger.warning("OpenAI batch embedding failed, falling back to sequential: %s", e)
            results: list[list[float] | None] = []
            for t in texts:
                vec = await self._embed_one(t.strip() if t else "", model, dimensions)
                results.append(vec)
            return results

    async def _embed_one(
        self, text: str, model: str, dimensions: int | None
    ) -> list[float] | None:
        if not text:
            return None
        try:
            kwargs: dict = {"model": model, "input": text}
            if dimensions is not None:
                kwargs["dimensions"] = dimensions
            resp = await self._client.embeddings.create(**kwargs)
            return list(resp.data[0].embedding) if resp.data else None
        except Exception as e:
            logger.warning("Embedding failed: %s", e)
            return None


class OpenAICompatibleProvider:
    """Uses OpenAI Responses API (supports hosted tools e.g. WebSearchTool)."""

    provider_type = "openai_compatible"

    def build(
        self,
        config: ProviderConfig,
        model_name: str,
        api_key: str | None,
    ) -> OpenAIResponsesModel:
        client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=api_key or "not-required",
            default_headers=config.default_headers or None,
            timeout=60.0,
        )
        return OpenAIResponsesModel(model=model_name, openai_client=client)

    async def health_check(
        self, config: ProviderConfig, api_key: str | None
    ) -> bool:
        try:
            client = AsyncOpenAI(
                base_url=config.base_url,
                api_key=api_key or "x",
                default_headers=config.default_headers or None,
                timeout=60.0,
            )
            await client.models.list()
            return True
        except Exception:
            return False

    def get_capability(
        self,
        cap: type,
        config: ProviderConfig,
        api_key: str | None,
    ):
        from core.llm.capabilities import EmbeddingCapability

        if cap is EmbeddingCapability:
            # For default OpenAI API (no custom base_url), a key is required
            if not config.base_url and not api_key:
                return None
            client = AsyncOpenAI(
                base_url=config.base_url,
                api_key=api_key or "not-required",
                default_headers=config.default_headers or None,
                timeout=60.0,
            )
            return OpenAIEmbedder(client)
        return None
