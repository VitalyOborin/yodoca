"""ModelRouter: bridge from config to Model instances. Core only."""

import logging
from typing import Any, Callable

from core.llm.protocol import ModelConfig, ModelProvider, ProviderConfig
from core.llm.providers import AnthropicProvider, OpenAICompatibleProvider

logger = logging.getLogger(__name__)


def _dict_to_provider_config(provider_id: str, data: dict[str, Any]) -> ProviderConfig:
    return ProviderConfig(
        id=provider_id,
        type=str(data.get("type", "openai_compatible")),
        base_url=data.get("base_url"),
        api_key_secret=data.get("api_key_secret"),
        api_key_literal=data.get("api_key_literal"),
        default_headers=dict(data.get("default_headers") or {}),
        supports_hosted_tools=bool(data.get("supports_hosted_tools", True)),
    )


def _dict_to_model_config(data: dict[str, Any], provider_id: str) -> ModelConfig:
    return ModelConfig(
        provider=provider_id or str(data.get("provider", "")),
        model=str(data.get("model", "")),
        temperature=float(data.get("temperature", 0.7)),
        max_tokens=data.get("max_tokens"),
        extra=dict(data.get("extra") or {}),
    )


class ModelRouter:
    """Resolves agent_id to SDK-compatible Model instance via config/settings.yaml."""

    def __init__(
        self,
        settings: dict[str, Any],
        secrets_getter: Callable[[str], str | None],
    ) -> None:
        self._secrets = secrets_getter
        self._provider_configs: dict[str, ProviderConfig] = {}
        self._agent_configs: dict[str, ModelConfig] = {}
        self._providers: dict[str, ModelProvider] = {}
        self._cache: dict[str, Any] = {}
        self._load(settings)
        self._register_defaults()

    def _load(self, settings: dict[str, Any]) -> None:
        for pid, pdata in (settings.get("providers") or {}).items():
            if isinstance(pdata, dict):
                self._provider_configs[str(pid)] = _dict_to_provider_config(
                    str(pid), pdata
                )
        for aid, adata in (settings.get("agents") or {}).items():
            if isinstance(adata, dict):
                provider_id = adata.get("provider")
                if provider_id:
                    self._agent_configs[str(aid)] = _dict_to_model_config(
                        adata, str(provider_id)
                    )

    def _register_defaults(self) -> None:
        openai_compat = OpenAICompatibleProvider()
        self._providers["openai"] = openai_compat
        self._providers["openai_compatible"] = openai_compat
        self._providers["anthropic"] = AnthropicProvider()

    def get_default_provider(self) -> str | None:
        """Return provider id from default agent config, or None."""
        cfg = self._agent_configs.get("default")
        return cfg.provider if cfg else None

    def register_agent_config(self, agent_id: str, config: dict[str, Any]) -> None:
        """Register agent config from extension manifest (agent_config block)."""
        provider_id = config.get("provider")
        if not provider_id:
            return
        self._agent_configs[agent_id] = _dict_to_model_config(
            config, str(provider_id)
        )

    def _resolve_key(self, cfg: ProviderConfig) -> str | None:
        if cfg.api_key_literal:
            return cfg.api_key_literal
        if cfg.api_key_secret:
            return self._secrets(cfg.api_key_secret)
        return None

    def get_model(self, agent_id: str) -> Any:
        """Return cached or newly built Model instance for the agent."""
        if agent_id in self._cache:
            return self._cache[agent_id]
        agent_cfg = self._agent_configs.get(agent_id) or self._agent_configs.get(
            "default"
        )
        if not agent_cfg:
            raise KeyError(
                f"No model config for agent_id={agent_id!r} and no 'default' in config/settings.yaml"
            )
        provider_cfg = self._provider_configs.get(agent_cfg.provider)
        if not provider_cfg:
            raise KeyError(
                f"Unknown provider {agent_cfg.provider!r} for agent_id={agent_id!r}"
            )
        provider = self._providers.get(provider_cfg.type)
        if not provider:
            raise KeyError(
                f"Unknown provider type {provider_cfg.type!r} for provider id {provider_cfg.id!r}"
            )
        api_key = self._resolve_key(provider_cfg)
        model_instance = provider.build(
            provider_cfg, agent_cfg.model, api_key
        )
        self._cache[agent_id] = model_instance
        return model_instance

    def get_provider_client(
        self,
        provider_id: str | None = None,
    ) -> "AsyncOpenAI | None":
        """Build a raw AsyncOpenAI client for a provider.

        Args:
            provider_id: Explicit provider ID. None = first openai_compatible with a valid key.

        Returns:
            AsyncOpenAI client or None if provider not found or no key.

        Used by extensions needing direct provider access (e.g., embedding).
        """
        from openai import AsyncOpenAI

        if provider_id:
            candidates = [provider_id]
        else:
            candidates = [
                pid
                for pid, cfg in self._provider_configs.items()
                if cfg.type == "openai_compatible"
            ]
        for pid in candidates:
            pcfg = self._provider_configs.get(pid)
            if not pcfg or pcfg.type != "openai_compatible":
                continue
            key = self._resolve_key(pcfg)
            if not key:
                continue
            return AsyncOpenAI(
                base_url=pcfg.base_url,
                api_key=key,
                default_headers=pcfg.default_headers or None,
                timeout=30.0,
            )
        return None

    def supports_hosted_tools(self, agent_id: str) -> bool:
        """Return True if the provider for this agent supports OpenAI hosted tool types."""
        agent_cfg = self._agent_configs.get(agent_id) or self._agent_configs.get("default")
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
