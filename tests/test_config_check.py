"""Tests for core.config_check."""

import tempfile
from pathlib import Path

import pytest
import yaml

from core.config_check import is_configured


def test_is_configured_missing_settings(tmp_path: Path) -> None:
    """When settings.yaml does not exist, returns False."""
    ok, reason = is_configured(project_root=tmp_path)
    assert ok is False
    assert "not found" in reason


def test_is_configured_empty_providers(tmp_path: Path) -> None:
    """When providers is empty, returns False."""
    (tmp_path / "config").mkdir()
    settings = {"providers": {}, "agents": {"default": {"provider": "openai", "model": "gpt-5"}}}
    (tmp_path / "config" / "settings.yaml").write_text(yaml.safe_dump(settings))
    ok, reason = is_configured(project_root=tmp_path)
    assert ok is False
    assert "No providers" in reason


def test_is_configured_provider_no_key(tmp_path: Path) -> None:
    """When provider has no API key, returns False."""
    (tmp_path / "config").mkdir()
    settings = {
        "providers": {
            "openai": {"type": "openai_compatible", "api_key_secret": "OPENAI_API_KEY"},
        },
        "agents": {"default": {"provider": "openai", "model": "gpt-5"}},
    }
    (tmp_path / "config" / "settings.yaml").write_text(yaml.safe_dump(settings))
    ok, reason = is_configured(project_root=tmp_path)
    assert ok is False
    assert "no API key" in reason


def test_is_configured_local_provider_ok(tmp_path: Path) -> None:
    """When local provider has api_key_literal, returns True."""
    (tmp_path / "config").mkdir()
    settings = {
        "providers": {
            "lm_studio": {
                "type": "openai_compatible",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key_literal": "lm-studio",
            },
        },
        "agents": {"default": {"provider": "lm_studio", "model": "local"}},
    }
    (tmp_path / "config" / "settings.yaml").write_text(yaml.safe_dump(settings))
    ok, reason = is_configured(project_root=tmp_path)
    assert ok is True
    assert reason == "ok"


def test_is_configured_with_env_key(tmp_path: Path) -> None:
    """When provider key is in .env, returns True."""
    (tmp_path / "config").mkdir()
    settings = {
        "providers": {
            "openai": {"type": "openai_compatible", "api_key_secret": "OPENAI_API_KEY"},
        },
        "agents": {"default": {"provider": "openai", "model": "gpt-5"}},
    }
    (tmp_path / "config" / "settings.yaml").write_text(yaml.safe_dump(settings))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-test123\n")
    ok, reason = is_configured(project_root=tmp_path)
    assert ok is True
    assert reason == "ok"


def test_is_configured_malformed_yaml(tmp_path: Path) -> None:
    """When settings.yaml is invalid YAML, returns False."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("not: valid: yaml: [")
    ok, reason = is_configured(project_root=tmp_path)
    assert ok is False
    assert "parse error" in reason


def test_is_configured_unknown_provider_reference(tmp_path: Path) -> None:
    """When agents.default references non-existent provider, returns False."""
    (tmp_path / "config").mkdir()
    settings = {
        "providers": {
            "openai": {"type": "openai_compatible", "api_key_secret": "OPENAI_API_KEY"},
        },
        "agents": {"default": {"provider": "nonexistent", "model": "gpt-5"}},
    }
    (tmp_path / "config" / "settings.yaml").write_text(yaml.safe_dump(settings))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-test123\n")
    ok, reason = is_configured(project_root=tmp_path)
    assert ok is False
    assert "unknown provider" in reason or "nonexistent" in reason
