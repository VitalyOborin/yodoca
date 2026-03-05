"""LiteLLM provider for OpenAI-compatible chat/completions-only endpoints."""

from typing import Any

from core.llm.protocol import ProviderConfig


class LiteLLMOpenAICompatibleProvider:
    """Use LiteLLM chat/completions path for OpenAI-compatible APIs."""

    provider_type = "litellm_openai_compatible"

    def build(
        self,
        config: ProviderConfig,
        model_name: str,
        api_key: str | None,
    ) -> Any:
        try:
            from agents.extensions.models.litellm_model import LitellmModel
        except ImportError as e:
            raise ImportError(
                "LiteLLM provider requires: pip install 'openai-agents[litellm]'"
            ) from e

        model_prefix = config.litellm_model_prefix or "openai"
        litellm_model = (
            model_name if "/" in model_name else f"{model_prefix}/{model_name}"
        )
        return LitellmModel(
            model=litellm_model,
            base_url=config.api_base or config.base_url,
            api_key=api_key,
        )

    async def health_check(self, config: ProviderConfig, api_key: str | None) -> bool:
        """No lightweight chat/completions ping; report True if model builds."""
        try:
            self.build(config, "gpt-4o-mini", api_key)
            return True
        except Exception:
            return False

    def get_capability(
        self,
        cap: type,
        config: ProviderConfig,
        api_key: str | None,
    ) -> None:
        """LiteLLM chat/completions path does not expose embedding capability here."""
        return None
