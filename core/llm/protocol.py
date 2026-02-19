"""LLM provider protocol and configuration dataclasses.

Core infrastructure: not extensible by extensions.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ModelConfig:
    """Per-agent model configuration (from config/settings.yaml or manifest)."""

    provider: str
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderConfig:
    """Provider configuration from config/settings.yaml."""

    id: str
    type: str  # openai_compatible | anthropic
    base_url: str | None = None
    api_key_secret: str | None = None
    api_key_literal: str | None = None
    default_headers: dict[str, str] = field(default_factory=dict)
    # False for local/third-party providers (LM Studio, OpenRouter, Anthropic, etc.)
    # that do not support OpenAI hosted tool types (web_search_preview, computer_use_preview).
    supports_hosted_tools: bool = True


@runtime_checkable
class ModelProvider(Protocol):
    """Contract for an LLM API provider. Returns SDK-compatible Model instances."""

    provider_type: str

    def build(
        self,
        config: ProviderConfig,
        model_name: str,
        api_key: str | None,
    ) -> Any:
        """Return a Model instance compatible with OpenAI Agents SDK."""
        ...

    async def health_check(
        self, config: ProviderConfig, api_key: str | None
    ) -> bool:
        """Check provider availability."""
        ...
