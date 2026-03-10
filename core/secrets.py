"""Secure secret storage via OS keyring. Provider-agnostic key-value store."""

import asyncio
import json
import logging
import os
from pathlib import Path

import keyring
from keyring.errors import KeyringError

logger = logging.getLogger(__name__)
SERVICE_NAME = "yodoca"
SECRET_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent
    / "sandbox"
    / "data"
    / "secrets"
    / "registry.json"
)


def _is_fail_backend() -> bool:
    """True when the active backend is the fail stub (no real keyring)."""
    try:
        from keyring.backends.fail import Keyring as FailKeyring

        backend = keyring.get_keyring()
        return isinstance(backend, FailKeyring)
    except Exception:
        return True


def is_keyring_available() -> bool:
    """True when a real OS keyring backend is active (not the fail stub)."""
    return not _is_fail_backend()


def _load_secret_registry() -> set[str]:
    if not SECRET_REGISTRY_PATH.exists():
        return set()
    try:
        data = json.loads(SECRET_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {item for item in data if isinstance(item, str) and item}


def _write_secret_registry(secret_names: set[str]) -> None:
    if not secret_names:
        try:
            SECRET_REGISTRY_PATH.unlink(missing_ok=True)
        except OSError:
            logger.debug("failed to delete secret registry", exc_info=True)
        return

    try:
        SECRET_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_REGISTRY_PATH.write_text(
            json.dumps(sorted(secret_names), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.debug("failed to update secret registry", exc_info=True)


def _register_secret_name(name: str) -> None:
    secret_names = _load_secret_registry()
    if name in secret_names:
        return
    secret_names.add(name)
    _write_secret_registry(secret_names)


def _unregister_secret_name(name: str) -> None:
    secret_names = _load_secret_registry()
    if name not in secret_names:
        return
    secret_names.remove(name)
    _write_secret_registry(secret_names)


def list_registered_secrets() -> set[str]:
    """Return names of secrets previously stored by the application."""
    return _load_secret_registry()


def get_secret(name: str) -> str | None:
    """Resolve secret: keyring -> os.environ. Sync, safe for init/scripts."""
    try:
        value = keyring.get_password(SERVICE_NAME, name)
        if value:
            return value
    except KeyringError:
        logger.debug("keyring lookup failed for %s, falling back to env", name)
    return os.environ.get(name)


async def get_secret_async(name: str) -> str | None:
    """Async variant -- wraps keyring I/O in to_thread to avoid blocking the event loop."""
    try:
        value = await asyncio.to_thread(keyring.get_password, SERVICE_NAME, name)
        if value:
            return value
    except KeyringError:
        logger.debug("keyring lookup failed for %s, falling back to env", name)
    return os.environ.get(name)


def set_secret(name: str, value: str) -> None:
    """Store secret in OS keyring. Raises KeyringError if backend unavailable."""
    keyring.set_password(SERVICE_NAME, name, value)
    _register_secret_name(name)


async def set_secret_async(name: str, value: str) -> None:
    """Async variant of set_secret."""
    await asyncio.to_thread(keyring.set_password, SERVICE_NAME, name, value)
    _register_secret_name(name)


def delete_secret(name: str) -> None:
    """Remove secret from keyring. No-op if absent."""
    try:
        keyring.delete_password(SERVICE_NAME, name)
    except KeyringError:
        pass
    _unregister_secret_name(name)
