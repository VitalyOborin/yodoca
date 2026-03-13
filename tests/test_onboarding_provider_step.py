"""Tests for onboarding.steps.provider_step."""

from unittest.mock import ANY, Mock, call, patch

from onboarding.state import WizardState
from onboarding.steps.provider_step import _collect_lm_studio


def test_collect_lm_studio_collects_api_key_and_base_url() -> None:
    """LM Studio collector should ask for base URL and API key."""
    state = WizardState()

    with patch(
        "questionary.text",
        side_effect=[
            Mock(ask=Mock(return_value="http://127.0.0.1:1234/v1")),
            Mock(ask=Mock(return_value="my-local-key")),
        ],
    ) as mock_text:
        result = _collect_lm_studio(state)

    assert result is True
    assert state.providers["lm_studio"] == {
        "type": "openai_compatible",
        "base_url": "http://127.0.0.1:1234/v1",
        "api_key_secret": "LM_STUDIO_API_KEY",
        "supports_hosted_tools": False,
    }
    assert state.env_vars["LM_STUDIO_API_KEY"] == "my-local-key"
    assert mock_text.call_args_list == [
        call(
            "LM Studio / local API base URL:",
            default="http://127.0.0.1:1234/v1",
            style=ANY,
        ),
        call(
            "Local model API key (optional, default: dummy):",
            default="dummy",
            style=ANY,
        ),
    ]


def test_collect_lm_studio_uses_dummy_for_empty_api_key() -> None:
    """LM Studio collector should store dummy key when user enters empty value."""
    state = WizardState()

    with patch(
        "questionary.text",
        side_effect=[
            Mock(ask=Mock(return_value="http://127.0.0.1:1234/v1")),
            Mock(ask=Mock(return_value="  ")),
        ],
    ):
        result = _collect_lm_studio(state)

    assert result is True
    assert state.env_vars["LM_STUDIO_API_KEY"] == "dummy"
