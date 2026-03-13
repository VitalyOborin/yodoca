"""OpenAI-compatible providers (OpenAI, OpenRouter, LM Studio, etc.)."""

import logging

from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel
from openai import AsyncOpenAI

from core.llm.protocol import ProviderConfig

logger = logging.getLogger(__name__)


# --- Client factory (single path for AsyncOpenAI construction) ---


def _make_openai_client(
    config: ProviderConfig,
    api_key: str | None,
    *,
    api_key_fallback: str = "not-required",
) -> AsyncOpenAI:
    """Build AsyncOpenAI from provider config. API-key fallback varies by call site."""
    return AsyncOpenAI(
        base_url=config.base_url,
        api_key=api_key or api_key_fallback,
        default_headers=config.default_headers or None,
        timeout=60.0,
    )


# --- Embedding helpers ---


def _normalize_texts(texts: list[str]) -> list[tuple[int, str]]:
    """Return (index, cleaned_text) for non-empty texts. Preserves original indices."""
    cleaned = [t.strip() if t else "" for t in texts]
    return [(i, t) for i, t in enumerate(cleaned) if t]


def _embed_request_kwargs(
    model: str,
    input_text: str | list[str],
) -> dict:
    """Build kwargs for embeddings.create. Handles single text or list."""
    return {"model": model, "input": input_text}


# --- OpenAIEmbedder ---


class OpenAIEmbedder:
    """Adapter implementing EmbeddingCapability for OpenAI-compatible APIs."""

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def embed_batch(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float] | None]:
        if not texts:
            return []
        non_empty = _normalize_texts(texts)
        if not non_empty:
            return [None] * len(texts)
        try:
            kwargs = _embed_request_kwargs(model, [t for _, t in non_empty])
            resp = await self._client.embeddings.create(**kwargs)
            result: list[list[float] | None] = [None] * len(texts)
            for emb_data, (orig_idx, _) in zip(resp.data, non_empty, strict=True):
                result[orig_idx] = list(emb_data.embedding)
            return result
        except Exception as e:
            logger.warning(
                "OpenAI batch embedding failed, falling back to sequential: %s", e
            )
            return await self._embed_batch_fallback(texts, model)

    async def _embed_batch_fallback(
        self, texts: list[str], model: str
    ) -> list[list[float] | None]:
        """Fallback: embed one by one when batch API fails."""
        results: list[list[float] | None] = []
        for t in texts:
            vec = await self._embed_one(t.strip() if t else "", model)
            results.append(vec)
        return results

    async def _embed_one(self, text: str, model: str) -> list[float] | None:
        if not text:
            return None
        try:
            kwargs = _embed_request_kwargs(model, text)
            resp = await self._client.embeddings.create(**kwargs)
            return list(resp.data[0].embedding) if resp.data else None
        except Exception as e:
            logger.warning("Embedding failed: %s", e)
            return None


class OpenAICompatibleProvider:
    """OpenAI-compatible. api_mode: responses (default) or chat_completions."""

    provider_type = "openai_compatible"

    def build(
        self,
        config: ProviderConfig,
        model_name: str,
        api_key: str | None,
    ) -> OpenAIResponsesModel | OpenAIChatCompletionsModel:
        client = _make_openai_client(config, api_key)
        if config.api_mode == "chat_completions":
            return OpenAIChatCompletionsModel(model=model_name, openai_client=client)
        return OpenAIResponsesModel(model=model_name, openai_client=client)

    async def health_check(self, config: ProviderConfig, api_key: str | None) -> bool:
        try:
            client = _make_openai_client(config, api_key, api_key_fallback="x")
            await client.models.list()
            return True
        except Exception:
            return False

    def get_capability(
        self,
        cap: type,
        config: ProviderConfig,
        api_key: str | None,
    ) -> OpenAIEmbedder | None:
        from core.llm.capabilities import EmbeddingCapability

        if cap is EmbeddingCapability:
            # For default OpenAI API (no custom base_url), a key is required
            if not config.base_url and not api_key:
                return None
            client = _make_openai_client(config, api_key)
            return OpenAIEmbedder(client)
        return None
