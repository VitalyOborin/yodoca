"""Tests for Pydantic AppSettings merge and validation."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from core.settings import (
    format_validation_errors,
    merge_settings_dict,
    validate_app_settings,
)
from core.settings_models import AppSettings


def test_validate_app_settings_roundtrip_defaults() -> None:
    merged = AppSettings().model_dump(mode="python")
    app = validate_app_settings(merged)
    assert isinstance(app, AppSettings)
    assert app.agents["default"].provider == "openai"


def test_validate_app_settings_rejects_invalid_logging_level_type() -> None:
    merged = AppSettings().model_dump(mode="python")
    merged["logging"]["level"] = ["not", "a", "string"]
    with pytest.raises(ValidationError) as exc_info:
        validate_app_settings(merged)
    text = format_validation_errors(exc_info.value)
    assert "logging" in text


def test_merge_settings_dict_overrides_defaults(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "settings.yaml").write_text(
        yaml.dump({"thread": {"timeout_sec": 99}}),
        encoding="utf-8",
    )
    merged = merge_settings_dict(cfg)
    assert merged["thread"]["timeout_sec"] == 99


def test_merge_settings_dict_deep_merges_agents(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "settings.yaml").write_text(
        yaml.dump({"agents": {"default": {"model": "gpt-4o-mini"}}}),
        encoding="utf-8",
    )
    merged = merge_settings_dict(cfg)
    assert merged["agents"]["default"]["model"] == "gpt-4o-mini"
    assert merged["agents"]["default"]["provider"] == "openai"
