"""Tests for onboarding.config_writer and embedding_step."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from core.settings import reload_settings
from onboarding.config_writer import write_config
from onboarding.state import WizardState


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Ensure clean settings cache for each test."""
    reload_settings()
    yield
    reload_settings()


def test_write_config_creates_settings_and_env(tmp_path: Path) -> None:
    """write_config atomically writes settings.yaml and .env."""
    state = WizardState(
        providers={
            "openai": {"type": "openai_compatible", "api_key_secret": "OPENAI_API_KEY"},
        },
        env_vars={"OPENAI_API_KEY": "sk-test"},
        agents={"default": {"provider": "openai", "model": "gpt-4o-mini"}},
        extensions={"embedding": {"provider": "openai", "default_model": "text-embedding-3-small"}},
    )
    settings_path = tmp_path / "config" / "settings.yaml"
    env_path = tmp_path / ".env"

    write_config(state, settings_path, env_path, tmp_path)

    assert settings_path.exists()
    data = yaml.safe_load(settings_path.read_text())
    assert data["providers"]["openai"]["type"] == "openai_compatible"
    assert data["agents"]["default"]["provider"] == "openai"
    assert data["agents"]["default"]["model"] == "gpt-5.2"
    assert data["agents"]["default"]["instructions"] == "prompts/default.jinja2"
    assert "embedding" not in data["agents"]
    assert data["extensions"]["embedding"]["provider"] == "openai"
    assert data["extensions"]["embedding"]["default_model"] == "text-embedding-3-large"

    assert env_path.exists()
    assert "OPENAI_API_KEY=sk-test" in env_path.read_text()

    assert (tmp_path / "sandbox" / ".restart_requested").exists()


def test_write_config_preserves_existing_env_keys(tmp_path: Path) -> None:
    """write_config merges new env vars with existing .env."""
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING_KEY=old\n")

    state = WizardState(
        providers={"lm_studio": {"type": "openai_compatible", "api_key_literal": "x"}},
        env_vars={"NEW_KEY": "new"},
        agents={"default": {"provider": "lm_studio", "model": "local"}},
    )
    settings_path = tmp_path / "config" / "settings.yaml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("providers: {}\nagents: {}\n")

    write_config(state, settings_path, env_path, tmp_path)

    content = env_path.read_text()
    assert "EXISTING_KEY=old" in content
    assert "NEW_KEY=new" in content


def test_run_embedding_step_uses_same_provider_and_sets_embedding_extension() -> None:
    """run_embedding_step populates state.extensions['embedding'] when user selects same provider."""
    state = WizardState(
        providers={"openai": {"type": "openai_compatible", "api_key_secret": "X"}},
        env_vars={},
        agents={"default": {"provider": "openai", "model": "gpt-4o-mini"}},
    )

    mock_prompt = type("MockPrompt", (), {"ask": lambda *a, **kw: True})()
    mock_select_prompt = type("MockPrompt", (), {"ask": lambda *a, **kw: "text-embedding-3-small"})()

    with (
        patch("questionary.confirm", return_value=mock_prompt),
        patch("questionary.select", return_value=mock_select_prompt),
    ):
        from onboarding.steps.embedding_step import run_embedding_step

        result = run_embedding_step(state)

    assert result is True
    assert state.extensions["embedding"] == {
        "provider": "openai",
        "default_model": "text-embedding-3-small",
    }
