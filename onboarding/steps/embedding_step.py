"""Embedding provider and model selection step.

Memory requires an embedding model. User can use the same provider as default
or select a different one (including adding a new provider).
"""

import questionary
from questionary import Choice

from onboarding.state import WizardState
from onboarding.steps.provider_step import (
    _ask_until_nonempty,
    add_provider_credentials_only,
    get_provider_choices_not_in_state,
)
from onboarding.ui import STYLE

# Embedding models per provider (only openai_compatible providers support embeddings)
_EMBEDDING_MODELS: dict[str, list[str]] = {
    "openai": [
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-ada-002",
    ],
    "openrouter": [
        "openai/text-embedding-3-small",
        "openai/text-embedding-3-large",
    ],
    "lm_studio": ["text-embedding-jina-embeddings-v5-text-small-retrieval"],
}

_ADD_NEW_PROVIDER = "__add_new__"
_MANUAL_ENTRY = "__manual__"


def run_embedding_step(state: WizardState) -> bool:
    """Ask for embedding provider and model. Returns False if cancelled."""
    print("\nThe memory system requires an embedding model.\n")

    default_provider = state.agents.get("default", {}).get("provider")
    embedding_capable = [p for p in state.providers if p in _EMBEDDING_MODELS]

    if not embedding_capable:
        # Default provider (e.g. Anthropic) may not support embeddings; offer to add one
        print(
            "The default provider does not support embeddings. "
            "Add a provider for embeddings (OpenAI, OpenRouter, or local).\n"
        )
        provider_id = _choose_or_add_embedding_provider(state, embedding_capable)
        if provider_id is None:
            return False
        model = _select_embedding_model(provider_id)
        if model is None:
            return False
        state.extensions["embedding"] = {
            "provider": provider_id,
            "default_model": model,
        }
        return True

    if default_provider in embedding_capable:
        use_same = questionary.confirm(
            "Use same provider as default?",
            default=True,
            style=STYLE,
        ).ask()
        if use_same is None:
            return False
    else:
        use_same = False

    if use_same:
        provider_id = default_provider
    else:
        provider_id = _choose_or_add_embedding_provider(state, embedding_capable)
        if provider_id is None:
            return False

    model = _select_embedding_model(provider_id)
    if model is None:
        return False

    state.extensions["embedding"] = {"provider": provider_id, "default_model": model}
    return True


def _choose_or_add_embedding_provider(
    state: WizardState, embedding_capable: list[str]
) -> str | None:
    """Let user pick an existing embedding-capable provider or add a new one. Returns provider_id or None."""
    choices: list[Choice] = [Choice(_label(p), p) for p in embedding_capable]
    remaining = get_provider_choices_not_in_state(state)
    # Only show "Add new provider" for types that support embeddings
    remaining_embedding = [
        (lbl, pid) for lbl, pid in remaining if pid in _EMBEDDING_MODELS
    ]
    if remaining_embedding:
        choices.append(Choice("Add new provider...", _ADD_NEW_PROVIDER))

    provider_id = questionary.select(
        "Select embedding provider:",
        choices=choices,
        style=STYLE,
    ).ask()
    if provider_id is None:
        return None

    if provider_id == _ADD_NEW_PROVIDER:
        add_choices = [Choice(lbl, pid) for lbl, pid in remaining_embedding]
        which = questionary.select(
            "Which provider to add?",
            choices=add_choices,
            style=STYLE,
        ).ask()
        if which is None:
            return None
        if not add_provider_credentials_only(state, which):
            return None
        return which

    return provider_id


def _label(provider_id: str) -> str:
    """Human-readable provider label."""
    labels = {
        "openai": "OpenAI",
        "openrouter": "OpenRouter",
        "lm_studio": "Local (LM Studio)",
    }
    return labels.get(provider_id, provider_id)


def _select_embedding_model(provider_id: str) -> str | None:
    """Select embedding model for provider. Returns model name or None if cancelled."""
    models = _EMBEDDING_MODELS.get(provider_id, [])
    if not models:
        return _ask_until_nonempty("Embedding model name:")

    choices: list[Choice] = [Choice(m, m) for m in models]
    choices.append(Choice("Enter model name manually...", _MANUAL_ENTRY))

    selected = questionary.select(
        "Embedding model:",
        choices=choices,
        style=STYLE,
    ).ask()
    if selected is None:
        return None
    if selected == _MANUAL_ENTRY:
        return _ask_until_nonempty("Embedding model name:")
    return selected
