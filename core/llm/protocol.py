"""LLM provider protocol and configuration dataclasses.

Core infrastructure: not extensible by extensions.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


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
class ModelRouterProtocol(Protocol):
    """Contract for model resolution. Used by Loader, ExtensionContext, CoreToolsProvider."""

    def get_model(self, agent_id: str) -> Any: ...
    def get_default_provider(self) -> str | None: ...
    def register_agent_config(self, agent_id: str, config: dict[str, Any]) -> None: ...
    def supports_hosted_tools(self, agent_id: str) -> bool: ...
    def get_capability(
        self, cap: type[T], provider_id: str | None = None
    ) -> T | None: ...


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

    async def health_check(self, config: ProviderConfig, api_key: str | None) -> bool:
        """Check provider availability."""
        ...

    def get_capability(
        self,
        cap: type[T],
        config: ProviderConfig,
        api_key: str | None,
    ) -> T | None:
        """Return a capability instance if this provider supports it, else None."""
        ...
