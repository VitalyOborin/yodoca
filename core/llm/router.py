"""ModelRouter: bridge from config to Model instances. Core only."""

import logging
from collections.abc import Callable
from typing import Any, TypeVar, cast

from core.llm.protocol import ModelConfig, ModelProvider, ProviderConfig
from core.llm.providers import AnthropicProvider, OpenAICompatibleProvider
from core.settings_models import AgentEntry, AppSettings, ProviderEntry

T = TypeVar("T")

logger = logging.getLogger(__name__)


# --- Settings parsing (internal, not part of public API) ---


def _provider_config_from_entry(provider_id: str, data: ProviderEntry) -> ProviderConfig:
    """Build ProviderConfig from validated settings entry."""
    return ProviderConfig(
        id=provider_id,
        type=data.type,
        api_mode=data.api_mode,
        base_url=data.base_url,
        api_key_secret=data.api_key_secret,
        api_key_literal=data.api_key_literal,
        default_headers=dict(data.default_headers),
        supports_hosted_tools=data.supports_hosted_tools,
    )


def _model_config_from_entry(data: AgentEntry, provider_id: str) -> ModelConfig:
    """Build ModelConfig from validated settings entry."""
    return ModelConfig(
        provider=provider_id or data.provider,
        model=data.model,
        temperature=data.temperature,
        max_tokens=data.max_tokens,
        extra=dict(data.extra),
    )


def _load_provider_configs(settings: AppSettings) -> dict[str, ProviderConfig]:
    """Extract provider configs from settings. Returns provider_id -> ProviderConfig."""
    return {
        str(pid): _provider_config_from_entry(str(pid), pdata)
        for pid, pdata in settings.providers.items()
    }


def _load_agent_configs(settings: AppSettings) -> dict[str, ModelConfig]:
    """Extract agent configs from settings. Returns agent_id -> ModelConfig."""
    result: dict[str, ModelConfig] = {}
    for aid, adata in settings.agents.items():
        if adata.provider:
            result[str(aid)] = _model_config_from_entry(adata, adata.provider)
    return result


def _build_provider_registry() -> dict[str, ModelProvider]:
    """Build default provider type -> instance mapping."""
    openai_compat = OpenAICompatibleProvider()
    return cast(
        dict[str, ModelProvider],
        {
            "openai": openai_compat,
            "openai_compatible": openai_compat,
            "anthropic": AnthropicProvider(),
        },
    )


# --- ModelRouter ---


class ModelRouter:
    """Resolves agent_id to SDK-compatible Model instance via config/settings.yaml."""

    def __init__(
        self,
        settings: AppSettings,
        secrets_getter: Callable[[str], str | None],
    ) -> None:
        self._secrets = secrets_getter
        self._provider_configs = _load_provider_configs(settings)
        self._agent_configs = _load_agent_configs(settings)
        self._providers = _build_provider_registry()
        self._cache: dict[str, Any] = {}

    def get_default_provider(self) -> str | None:
        """Return provider id from default agent config, or None."""
        cfg = self._agent_configs.get("default")
        return cfg.provider if cfg else None

    def get_default_agent_config(self) -> dict[str, Any] | None:
        """Return default agent config for dynamic agents when model is omitted."""
        cfg = self._agent_configs.get("default")
        if not cfg:
            return None
        return {"provider": cfg.provider, "model": cfg.model}

    def register_agent_config(self, agent_id: str, config: dict[str, Any]) -> None:
        """Register agent config from extension manifest (agent_config block).

        settings.yaml takes priority — only register if not already configured.
        """
        if agent_id in self._agent_configs:
            return
        provider_id = config.get("provider")
        if not provider_id:
            return
        entry = AgentEntry.model_validate(config)
        self._agent_configs[agent_id] = _model_config_from_entry(
            entry, str(provider_id)
        )

    def remove_agent_config(self, agent_id: str) -> None:
        """Remove a dynamic agent's config and cached model instance."""
        self._agent_configs.pop(agent_id, None)
        self._cache.pop(agent_id, None)

    def _resolve_key(self, cfg: ProviderConfig) -> str | None:
        if cfg.api_key_literal:
            return cfg.api_key_literal
        if cfg.api_key_secret:
            return self._secrets(cfg.api_key_secret)
        return None

    def _resolve_agent_config(self, agent_id: str) -> ModelConfig | None:
        """Resolve agent config for agent_id, falling back to default."""
        return self._agent_configs.get(agent_id) or self._agent_configs.get("default")

    def get_model(self, agent_id: str) -> Any:
        """Return cached or newly built Model instance for the agent."""
        if agent_id in self._cache:
            return self._cache[agent_id]
        agent_cfg = self._resolve_agent_config(agent_id)
        if not agent_cfg:
            raise KeyError(
                f"No model config for agent_id={agent_id!r} and no "
                "'default' in config/settings.yaml"
            )
        provider_cfg = self._provider_configs.get(agent_cfg.provider)
        if not provider_cfg:
            raise KeyError(
                f"Unknown provider {agent_cfg.provider!r} for agent_id={agent_id!r}"
            )
        provider = self._providers.get(provider_cfg.type)
        if not provider:
            raise KeyError(
                f"Unknown provider type {provider_cfg.type!r} "
                f"for provider id {provider_cfg.id!r}"
            )
        api_key = self._resolve_key(provider_cfg)
        model_instance = provider.build(provider_cfg, agent_cfg.model, api_key)
        self._cache[agent_id] = model_instance
        return model_instance

    def get_capability(
        self,
        cap: type[T],
        provider_id: str | None = None,
    ) -> T | None:
        """Return a capability instance from a provider that supports it.

        Args:
            cap: Capability protocol type (e.g. EmbeddingCapability).
            provider_id: Explicit provider ID.
                None = first provider supporting the capability.

        Returns:
            Capability instance or None if no provider supports it.
        """
        if provider_id:
            candidates = [provider_id]
        else:
            candidates = list(self._provider_configs.keys())

        for pid in candidates:
            pcfg = self._provider_configs.get(pid)
            if not pcfg:
                continue
            provider = self._providers.get(pcfg.type)
            if not provider:
                continue
            key = self._resolve_key(pcfg)
            result = provider.get_capability(cap, pcfg, key)
            if result is not None:
                return result
        return None

    def supports_hosted_tools(self, agent_id: str) -> bool:
        """Return True if the provider supports OpenAI hosted tool types."""
        agent_cfg = self._resolve_agent_config(agent_id)
        if not agent_cfg:
            return True
        provider_cfg = self._provider_configs.get(agent_cfg.provider)
        if not provider_cfg:
            return True
        return provider_cfg.supports_hosted_tools

    def invalidate(self, agent_id: str | None = None) -> None:
        """Invalidate cache after config change (hot-reload)."""
        if agent_id:
            self._cache.pop(agent_id, None)
        else:
            self._cache.clear()

    async def health_check_all(self) -> dict[str, bool]:
        """Check each configured provider; return provider_id -> ok."""
        results: dict[str, bool] = {}
        for pid, pcfg in self._provider_configs.items():
            provider = self._providers.get(pcfg.type)
            if provider:
                key = self._resolve_key(pcfg)
                try:
                    results[pid] = await provider.health_check(pcfg, key)
                except Exception as e:
                    logger.debug("health_check %s: %s", pid, e)
                    results[pid] = False
        return results
