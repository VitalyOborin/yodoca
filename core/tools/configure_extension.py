"""Tool to configure extensions that implement SetupProvider."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from agents import function_tool
from pydantic import BaseModel

from core.extensions.contract import SetupProvider


class ConfigureExtensionResult(BaseModel):
    """Result of configure_extension tool."""

    success: bool
    message: str = ""
    error: str | None = None


_SECRET_ID_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def _is_secret_param(ext: SetupProvider, param_name: str) -> bool:
    """Check whether *param_name* is marked as secret."""
    for param in ext.get_setup_schema():
        if param.get("name") == param_name:
            return bool(param.get("secret"))
    return False


async def _resolve_secret_with_wait(
    resolver: Callable[[str], Awaitable[str | None]],
    secret_id: str,
    timeout_sec: float = 90.0,
    poll_interval_sec: float = 0.1,
) -> str | None:
    """Resolve a secret reference, waiting briefly for async secure-input flow."""
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while True:
        value = await resolver(secret_id)
        if value:
            return value
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(poll_interval_sec)


def _looks_like_secret_id(value: str) -> bool:
    return bool(_SECRET_ID_PATTERN.match(value))


def make_configure_extension_tool(
    extensions: dict[str, Any],
    secret_resolver: Callable[[str], Awaitable[str | None]] | None = None,
) -> Any:
    """Create configure_extension tool bound to the given extensions dict.

    *secret_resolver* – optional async callable that looks up a secret by name
    (keyring / env).  When the LLM passes a ``secret_id`` reference (from
    ``request_secure_input``) as *value* for a secret parameter, the tool
    resolves it to the real secret before forwarding to ``apply_config``.
    """

    @function_tool(name_override="configure_extension")
    async def configure_extension(
        extension_id: str,
        param_name: str,
        value: str,
    ) -> ConfigureExtensionResult:
        """Configure an extension that requires setup. Saves value and verifies.
        extension_id: extension ID (e.g. 'telegram_channel', 'web_search').
        param_name: param from setup schema (e.g. 'token', 'tavily_api_key').
        value: value to save. For secrets, pass the secret_id
        from request_secure_input (auto-resolved)."""
        ext = extensions.get(extension_id)
        if ext is None:
            return ConfigureExtensionResult(
                success=False,
                error=f"Extension '{extension_id}' not found.",
            )
        if not isinstance(ext, SetupProvider):
            return ConfigureExtensionResult(
                success=False,
                error=f"Extension '{extension_id}' is not a SetupProvider.",
            )

        resolved_value = value
        if secret_resolver and _is_secret_param(ext, param_name):
            try:
                if _looks_like_secret_id(value):
                    secret = await _resolve_secret_with_wait(secret_resolver, value)
                else:
                    secret = await secret_resolver(value)
                if secret:
                    resolved_value = secret
            except Exception:
                pass

        try:
            await ext.apply_config(param_name, resolved_value)
        except Exception as e:
            return ConfigureExtensionResult(
                success=False,
                error=f"apply_config failed: {e}",
            )
        try:
            ok, msg = await ext.on_setup_complete()
            if ok:
                return ConfigureExtensionResult(success=True, message=msg)
            return ConfigureExtensionResult(success=False, error=msg)
        except Exception as e:
            return ConfigureExtensionResult(
                success=False,
                error=f"Verification failed: {e}",
            )

    return configure_extension
