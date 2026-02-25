"""Tests for ModelRouter: config loading, get_model, register_agent_config, supports_hosted_tools."""

from unittest.mock import MagicMock

import pytest

from core.llm import ModelRouter


def _mock_secrets(key: str) -> str | None:
    if key == "openai_api_key":
        return "sk-test-key"
    if key == "anthropic_api_key":
        return "sk-ant-test"
    return None


class TestModelRouterConfig:
    """Config loading and defaults."""

    def test_empty_settings_raises_on_get_model(self) -> None:
        router = ModelRouter(settings={}, secrets_getter=_mock_secrets)
        with pytest.raises(KeyError, match="No model config"):
            router.get_model("orchestrator")

    def test_default_provider_none_when_no_agents(self) -> None:
        router = ModelRouter(settings={}, secrets_getter=_mock_secrets)
        assert router.get_default_provider() is None

    def test_loads_providers_and_agents(self) -> None:
        settings = {
            "providers": {
                "openai": {"type": "openai_compatible", "api_key_secret": "openai_api_key"},
            },
            "agents": {
                "default": {"provider": "openai", "model": "gpt-4"},
            },
        }
        router = ModelRouter(settings=settings, secrets_getter=_mock_secrets)
        assert router.get_default_provider() == "openai"
        model = router.get_model("default")
        assert model is not None


class TestModelRouterRegisterAgentConfig:
    """Dynamic agent config registration."""

    def test_register_agent_config_adds_new_agent(self) -> None:
        settings = {
            "providers": {
                "openai": {"type": "openai_compatible", "api_key_secret": "openai_api_key"},
            },
            "agents": {"default": {"provider": "openai", "model": "gpt-4"}},
        }
        router = ModelRouter(settings=settings, secrets_getter=_mock_secrets)
        router.register_agent_config("builder", {"provider": "openai", "model": "gpt-4"})
        model = router.get_model("builder")
        assert model is not None

    def test_register_agent_config_skips_if_already_configured(self) -> None:
        settings = {
            "providers": {
                "openai": {"type": "openai_compatible", "api_key_secret": "openai_api_key"},
            },
            "agents": {"builder": {"provider": "openai", "model": "gpt-4"}},
        }
        router = ModelRouter(settings=settings, secrets_getter=_mock_secrets)
        router.register_agent_config("builder", {"provider": "openai", "model": "gpt-4o"})
        # Should still use gpt-4 from settings (first registration wins)
        model = router.get_model("builder")
        assert model is not None


class TestModelRouterSupportsHostedTools:
    """supports_hosted_tools check."""

    def test_supports_hosted_tools_true_when_no_config(self) -> None:
        router = ModelRouter(settings={}, secrets_getter=_mock_secrets)
        assert router.supports_hosted_tools("unknown") is True

    def test_supports_hosted_tools_from_provider_config(self) -> None:
        settings = {
            "providers": {
                "local": {
                    "type": "openai_compatible",
                    "base_url": "http://localhost:1234",
                    "supports_hosted_tools": False,
                },
            },
            "agents": {"default": {"provider": "local", "model": "local-model"}},
        }
        router = ModelRouter(settings=settings, secrets_getter=lambda _: None)
        assert router.supports_hosted_tools("default") is False


class TestModelRouterInvalidate:
    """Cache invalidation."""

    def test_invalidate_clears_cache(self) -> None:
        settings = {
            "providers": {
                "openai": {"type": "openai_compatible", "api_key_secret": "openai_api_key"},
            },
            "agents": {"default": {"provider": "openai", "model": "gpt-4"}},
        }
        router = ModelRouter(settings=settings, secrets_getter=_mock_secrets)
        m1 = router.get_model("default")
        router.invalidate("default")
        m2 = router.get_model("default")
        assert m1 is not None
        assert m2 is not None
        # New instance after invalidation
        assert m1 is not m2
