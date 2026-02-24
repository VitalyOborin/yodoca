"""Lightweight API probe for provider verification."""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def probe_openai_compatible(
    base_url: str,
    api_key: str,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """Probe an OpenAI-compatible API (OpenAI, OpenRouter, LM Studio).

    Returns (success, message).
    """
    url = base_url.rstrip("/") + "/models"
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 401:
                return False, "Invalid API key"
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}"
            data = resp.json()
            models = data.get("data", [])
            count = len(models) if isinstance(models, list) else 0
            return True, f"connected ({count} models)" if count else "connected"
    except httpx.TimeoutException:
        return False, "Connection timeout"
    except Exception as e:
        logger.debug("Probe failed: %s", e)
        return False, str(e)


async def probe_anthropic(api_key: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Probe Anthropic API via models endpoint (free, no completion cost).

    Returns (success, message).
    """
    url = "https://api.anthropic.com/v1/models"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 401:
                return False, "Invalid API key"
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}"
            data = resp.json()
            models = data.get("data", [])
            count = len(models) if isinstance(models, list) else 0
            return True, f"connected ({count} models)" if count else "connected"
    except httpx.TimeoutException:
        return False, "Connection timeout"
    except Exception as e:
        logger.debug("Probe failed: %s", e)
        return False, str(e)


async def probe_provider(
    provider_id: str,
    config: dict[str, Any],
    env_vars: dict[str, str],
) -> tuple[bool, str]:
    """Probe a provider by id and config.

    Returns (success, message).
    """
    ptype = config.get("type", "openai_compatible")
    api_key = config.get("api_key_literal") or env_vars.get(
        config.get("api_key_secret") or ""
    )
    base_url = config.get("base_url") or "https://api.openai.com/v1"

    if ptype == "anthropic":
        if not api_key:
            return False, "No API key"
        return await probe_anthropic(api_key)

    if ptype == "openai_compatible":
        key = api_key or "not-required"
        return await probe_openai_compatible(base_url, key)

    return False, f"Unknown provider type {ptype}"


async def probe_all(
    providers: dict[str, dict[str, Any]],
    env_vars: dict[str, str],
) -> dict[str, tuple[bool, str]]:
    """Probe all configured providers. Returns provider_id -> (ok, message)."""
    tasks = {
        pid: probe_provider(pid, cfg, env_vars)
        for pid, cfg in providers.items()
        if isinstance(cfg, dict)
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    out: dict[str, tuple[bool, str]] = {}
    for pid, res in zip(tasks, results):
        if isinstance(res, Exception):
            out[pid] = (False, str(res))
        else:
            out[pid] = res
    return out
