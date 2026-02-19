"""Configure OpenAI Agents SDK to use a custom base_url (and optional api_key) from settings.

Must be called once at app startup, before any Agent or Runner is created/used.
Uses set_default_openai_client() so all agents use the same endpoint (e.g. LM Studio).
"""

import os

from core.settings import get_setting, load_settings


def configure_openai_agents_sdk() -> None:
    """Set the default OpenAI client for the Agents SDK from config/settings.yaml.

    Reads agents.orchestrator.base_url (and optional api_key from env OPENAI_API_KEY).
    Disables tracing so the SDK does not send traces to external services when using
    a local endpoint.
    """
    from openai import AsyncOpenAI

    from agents import set_default_openai_client, set_tracing_disabled

    settings = load_settings()
    base_url = get_setting(settings, "agents.orchestrator.base_url")
    if not base_url or not isinstance(base_url, str):
        return
    base_url = base_url.strip()
    if not base_url:
        return

    api_key = os.environ.get("OPENAI_API_KEY", "")
    # Local endpoints (e.g. LM Studio) often accept any non-empty key
    if not api_key and not base_url.startswith("https://api.openai.com"):
        api_key = "lm-studio"

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)
