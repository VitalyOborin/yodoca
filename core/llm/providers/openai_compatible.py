"""OpenAI and OpenAI-compatible providers (OpenAI, OpenRouter, etc.). Uses Responses API by default."""

from openai import AsyncOpenAI

from agents import OpenAIResponsesModel
from core.llm.protocol import ProviderConfig


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
            )
            await client.models.list()
            return True
        except Exception:
            return False
