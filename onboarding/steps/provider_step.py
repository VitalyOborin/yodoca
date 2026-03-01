"""Provider selection and credential collection step.

Sequential add-one-at-a-time flow: select provider -> credentials -> model -> add another?
"""

from typing import Any, Callable

import questionary
from questionary import Choice

from onboarding.state import WizardState
from onboarding.ui import STYLE

_ALL_PROVIDERS = [
    ("OpenAI (GPT-5.2, GPT-5.1, GPT-5-mini, ...)", "openai"),
    ("Anthropic (Opus, Sonnet, Haiku)", "anthropic"),
    ("OpenRouter (200+ models)", "openrouter"),
    ("Local (LM Studio, Ollama, ...)", "lm_studio"),
]

_PROVIDER_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-5.2", "gpt-5.1", "gpt-5-mini", "gpt-4o", "gpt-4o-mini"],
    "anthropic": [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
    ],
    "openrouter": [
        "openai/gpt-5.2",
        "openai/gpt-5-mini",
        "anthropic/claude-sonnet-4-6",
    ],
    "lm_studio": ["zai-org/glm-4.7-flash", "qwen/qwen3.5-35b-a3b"],
}

_MANUAL_ENTRY = "__manual__"


def run_provider_step(state: WizardState) -> bool:
    """Run sequential provider add loop. Returns False if user cancelled."""
    print("\nWelcome to Yodoca setup!\n")

    while True:
        remaining = [
            Choice(label, pid)
            for label, pid in _ALL_PROVIDERS
            if pid not in state.providers
        ]
        if not remaining:
            break

        prompt = (
            "Select a provider:" if not state.providers else "Select another provider:"
        )
        choice = questionary.select(prompt, choices=remaining, style=STYLE).ask()
        if choice is None:
            return False

        if not _add_provider(state, choice):
            return False

        add_more = questionary.confirm(
            "Add another provider?",
            default=False,
            style=STYLE,
        ).ask()
        if add_more is None:
            return False
        if not add_more:
            break

    return True


_CREDENTIAL_COLLECTORS: dict[str, "Callable[[WizardState], bool]"] = {}


def add_provider_credentials_only(state: WizardState, provider_id: str) -> bool:
    """Add one provider (credentials only, no model). For use by embedding step.
    Returns False if user cancelled."""
    collector = _CREDENTIAL_COLLECTORS.get(provider_id)
    if collector is None:
        return False
    return collector(state)


def get_provider_choices_not_in_state(state: WizardState) -> list[tuple[str, str]]:
    """Return (label, provider_id) for providers not yet in state. For 'Add new provider' menu."""
    return [(label, pid) for label, pid in _ALL_PROVIDERS if pid not in state.providers]


def _add_provider(state: WizardState, provider_id: str) -> bool:
    """Add one provider: credentials + model. Returns False if cancelled."""
    if not add_provider_credentials_only(state, provider_id):
        return False

    model = _select_model(provider_id)
    if model is None:
        return False

    if "default" not in state.agents:
        state.agents["default"] = {"provider": provider_id, "model": model}

    return True


def _select_model(provider_id: str) -> str | None:
    """Select model for provider. Returns model name or None if cancelled."""
    models = _PROVIDER_MODELS.get(provider_id, [])
    if not models:
        return _ask_until_nonempty("Model name:")

    choices: list[Choice] = [Choice(m, m) for m in models]
    choices.append(Choice("Enter model name manually...", _MANUAL_ENTRY))

    selected = questionary.select(
        "Default model:",
        choices=choices,
        style=STYLE,
    ).ask()
    if selected is None:
        return None
    if selected == _MANUAL_ENTRY:
        return _ask_until_nonempty("Model name:")
    return selected


def _ask_until_nonempty(prompt: str, is_password: bool = False) -> str | None:
    """Prompt until non-empty input or user cancelled. Returns None on cancel."""
    while True:
        if is_password:
            val = questionary.password(prompt, style=STYLE).ask()
        else:
            val = questionary.text(prompt, style=STYLE).ask()
        if val is None:
            return None
        if val and val.strip():
            return val.strip()
        print("This field cannot be empty. Try again.\n")


def _collect_openai(state: WizardState) -> bool:
    """Collect OpenAI API key. Returns False if user cancelled."""
    key = _ask_until_nonempty("OpenAI API key:", is_password=True)
    if key is None:
        return False
    state.env_vars["OPENAI_API_KEY"] = key
    state.providers["openai"] = {
        "type": "openai_compatible",
        "api_key_secret": "OPENAI_API_KEY",
    }
    return True


def _collect_anthropic(state: WizardState) -> bool:
    """Collect Anthropic API key. Returns False if user cancelled."""
    key = _ask_until_nonempty("Anthropic API key:", is_password=True)
    if key is None:
        return False
    state.env_vars["ANTHROPIC_API_KEY"] = key
    state.providers["anthropic"] = {
        "type": "anthropic",
        "api_key_secret": "ANTHROPIC_API_KEY",
    }
    return True


def _collect_openrouter(state: WizardState) -> bool:
    """Collect OpenRouter API key. Returns False if user cancelled."""
    key = _ask_until_nonempty("OpenRouter API key:", is_password=True)
    if key is None:
        return False
    state.env_vars["OPENROUTER_API_KEY"] = key
    state.providers["openrouter"] = {
        "type": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_secret": "OPENROUTER_API_KEY",
        "default_headers": {
            "HTTP-Referer": "https://yodoca.app",
            "X-Title": "Yodoca",
        },
    }
    return True


def _collect_lm_studio(state: WizardState) -> bool:
    """Collect LM Studio base URL. Returns False if user cancelled."""
    base_url = questionary.text(
        "LM Studio / local API base URL:",
        default="http://127.0.0.1:1234/v1",
        style=STYLE,
    ).ask()
    if base_url is None:
        return False
    if base_url.strip():
        state.providers["lm_studio"] = {
            "type": "openai_compatible",
            "base_url": base_url.strip().rstrip("/"),
            "api_key_literal": "lm-studio",
            "supports_hosted_tools": False,
        }
    return True


_CREDENTIAL_COLLECTORS.update({
    "openai": _collect_openai,
    "anthropic": _collect_anthropic,
    "openrouter": _collect_openrouter,
    "lm_studio": _collect_lm_studio,
})
