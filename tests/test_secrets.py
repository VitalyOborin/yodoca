"""Tests for core.secrets module."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core import secrets


def test_get_secret_fallback_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When keyring returns None, fall back to os.environ."""
    monkeypatch.setenv("TEST_SECRET_ENV", "from-env")
    with patch("core.secrets.keyring") as mock_kr:
        mock_kr.get_password.return_value = None
        assert secrets.get_secret("TEST_SECRET_ENV") == "from-env"
    monkeypatch.delenv("TEST_SECRET_ENV", raising=False)


def test_get_secret_prefers_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """When keyring has value, it takes precedence over env."""
    monkeypatch.setenv("TEST_SECRET_BOTH", "from-env")
    with patch("core.secrets.keyring") as mock_kr:
        mock_kr.get_password.return_value = "from-keyring"
        assert secrets.get_secret("TEST_SECRET_BOTH") == "from-keyring"
    monkeypatch.delenv("TEST_SECRET_BOTH", raising=False)


def test_get_secret_keyring_error_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """When keyring raises KeyringError, fall back to env."""
    from keyring.errors import KeyringError

    monkeypatch.setenv("TEST_SECRET_ERR", "from-env")
    with patch("core.secrets.keyring") as mock_kr:
        mock_kr.get_password.side_effect = KeyringError("fail")
        assert secrets.get_secret("TEST_SECRET_ERR") == "from-env"
    monkeypatch.delenv("TEST_SECRET_ERR", raising=False)


@pytest.mark.asyncio
async def test_get_secret_async_prefers_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async variant prefers keyring over env."""
    monkeypatch.setenv("TEST_ASYNC", "from-env")
    with patch("core.secrets.keyring") as mock_kr:
        mock_kr.get_password.return_value = "from-keyring"
        result = await secrets.get_secret_async("TEST_ASYNC")
        assert result == "from-keyring"
    monkeypatch.delenv("TEST_ASYNC", raising=False)


def test_is_keyring_available_detects_fail_backend() -> None:
    """is_keyring_available returns False when fail backend is active."""
    with patch("core.secrets._is_fail_backend", return_value=True):
        assert secrets.is_keyring_available() is False
    with patch("core.secrets._is_fail_backend", return_value=False):
        assert secrets.is_keyring_available() is True


def test_set_secret_registers_name(tmp_path: Path) -> None:
    """Stored secrets are tracked so maintenance scripts can clear them later."""
    registry_path = tmp_path / "registry.json"
    with (
        patch("core.secrets.SECRET_REGISTRY_PATH", registry_path),
        patch("core.secrets.keyring") as mock_kr,
    ):
        secrets.set_secret("TEST_SECRET", "value")

    assert json.loads(registry_path.read_text(encoding="utf-8")) == ["TEST_SECRET"]
    mock_kr.set_password.assert_called_once_with("yodoca", "TEST_SECRET", "value")


def test_delete_secret_unregisters_name(tmp_path: Path) -> None:
    """Deleted secrets are removed from the registry."""
    registry_path = tmp_path / "registry.json"
    registry_path.write_text('["TEST_SECRET"]\n', encoding="utf-8")

    with (
        patch("core.secrets.SECRET_REGISTRY_PATH", registry_path),
        patch("core.secrets.keyring") as mock_kr,
    ):
        secrets.delete_secret("TEST_SECRET")

    assert not registry_path.exists()
    mock_kr.delete_password.assert_called_once_with("yodoca", "TEST_SECRET")
