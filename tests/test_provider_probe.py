"""Tests for onboarding.provider_probe."""

import pytest

from onboarding.provider_probe import (
    probe_all,
    probe_anthropic,
    probe_openai_compatible,
    probe_provider,
)


@pytest.mark.asyncio
async def test_probe_openai_compatible_success(httpx_mock) -> None:
    """OpenAI-compatible probe returns success on 200 with models."""
    httpx_mock.add_response(
        url="https://api.openai.com/v1/models",
        json={"data": [{"id": "gpt-5"}, {"id": "gpt-4"}]},
    )
    ok, msg = await probe_openai_compatible("https://api.openai.com/v1", "sk-test")
    assert ok is True
    assert "2 models" in msg


@pytest.mark.asyncio
async def test_probe_openai_compatible_401(httpx_mock) -> None:
    """OpenAI-compatible probe returns failure on 401."""
    httpx_mock.add_response(url="https://api.openai.com/v1/models", status_code=401)
    ok, msg = await probe_openai_compatible("https://api.openai.com/v1", "sk-bad")
    assert ok is False
    assert "Invalid" in msg


@pytest.mark.asyncio
async def test_probe_anthropic_success(httpx_mock) -> None:
    """Anthropic probe returns success on 200 with models."""
    httpx_mock.add_response(
        url="https://api.anthropic.com/v1/models",
        json={"data": [{"id": "claude-3-5-sonnet"}]},
    )
    ok, msg = await probe_anthropic("sk-ant-test")
    assert ok is True
    assert "1 models" in msg or "connected" in msg


@pytest.mark.asyncio
async def test_probe_provider_openai_compatible(httpx_mock) -> None:
    """probe_provider routes openai_compatible correctly."""
    httpx_mock.add_response(
        url="https://api.openai.com/v1/models",
        json={"data": []},
    )
    ok, msg = await probe_provider(
        "openai",
        {"type": "openai_compatible", "api_key_secret": "OPENAI_API_KEY"},
        {"OPENAI_API_KEY": "sk-test"},
    )
    assert ok is True


@pytest.mark.asyncio
async def test_probe_all_returns_per_provider_results(httpx_mock) -> None:
    """probe_all returns dict of provider_id -> (ok, message)."""
    httpx_mock.add_response(url="https://api.openai.com/v1/models", json={"data": []})
    httpx_mock.add_response(
        url="https://api.anthropic.com/v1/models", json={"data": []}
    )

    providers = {
        "openai": {"type": "openai_compatible", "api_key_secret": "OPENAI_API_KEY"},
        "anthropic": {"type": "anthropic", "api_key_secret": "ANTHROPIC_API_KEY"},
    }
    env = {"OPENAI_API_KEY": "sk-o", "ANTHROPIC_API_KEY": "sk-a"}

    results = await probe_all(providers, env)
    assert "openai" in results
    assert "anthropic" in results
    assert results["openai"][0] is True
    assert results["anthropic"][0] is True
