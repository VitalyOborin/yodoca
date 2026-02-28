"""Anthropic provider via LiteLLM (openai-agents[litellm])."""

from core.llm.protocol import ProviderConfig


class AnthropicProvider:
    """Anthropic API via LiteLLM. Requires: pip install 'openai-agents[litellm]'."""

    provider_type = "anthropic"

    def build(
        self,
        config: ProviderConfig,
        model_name: str,
        api_key: str | None,
    ):
        try:
            from agents.extensions.models.litellm_model import LitellmModel
        except ImportError as e:
            raise ImportError(
                "Anthropic provider requires: pip install 'openai-agents[litellm]'"
            ) from e
        # LiteLLM model name format: anthropic/claude-3-5-sonnet-...
        litellm_model = (
            model_name
            if model_name.startswith("anthropic/")
            else f"anthropic/{model_name}"
        )
        return LitellmModel(model=litellm_model, api_key=api_key)

    async def health_check(self, config: ProviderConfig, api_key: str | None) -> bool:
        """No lightweight LiteLLM ping; report True if build succeeds."""
        try:
            self.build(config, "claude-3-5-haiku-20241022", api_key)
            return True
        except Exception:
            return False

    def get_capability(
        self,
        cap: type,
        config: ProviderConfig,
        api_key: str | None,
    ):
        """Anthropic does not support embedding capability."""
        return None
