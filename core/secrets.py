"""Secure secret storage via OS keyring. Provider-agnostic key-value store."""

import asyncio
import logging
import os

import keyring
from keyring.errors import KeyringError

logger = logging.getLogger(__name__)
SERVICE_NAME = "yodoca"


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


async def set_secret_async(name: str, value: str) -> None:
    """Async variant of set_secret."""
    await asyncio.to_thread(keyring.set_password, SERVICE_NAME, name, value)


def delete_secret(name: str) -> None:
    """Remove secret from keyring. No-op if absent."""
    try:
        keyring.delete_password(SERVICE_NAME, name)
    except KeyringError:
        pass
