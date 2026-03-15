"""Tool to request secure input from user via channel interceptor. Secret never reaches LLM."""

import re
from typing import TYPE_CHECKING, Any

from agents import function_tool
from pydantic import BaseModel

from core.events.topics import SystemTopics

if TYPE_CHECKING:
    from core.events.bus import EventBus

_VALID_ID_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9_]")
_LEADING_NON_ALPHA_RE = re.compile(r"^[^a-zA-Z_]+")

_MAX_SECRET_ID_LEN = 64


class SecureInputResult(BaseModel):
    """Result of request_secure_input tool."""

    success: bool
    secret_id: str = ""
    message: str = ""
    error: str | None = None


def _sanitize_secret_id(raw: str) -> str:
    """Normalize secret_id to [a-zA-Z_][a-zA-Z0-9_]* (max 64 chars).

    Replaces dots, dashes, @, and other non-alphanumeric chars with underscores,
    strips leading digits, and collapses consecutive underscores.
    """
    sanitized = _NON_ALNUM_RE.sub("_", raw)
    sanitized = _LEADING_NON_ALPHA_RE.sub("", sanitized)
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = sanitized.strip("_")
    return sanitized[:_MAX_SECRET_ID_LEN]


def _validate_secret_id(secret_id: str) -> str | None:
    """Return error message if invalid after sanitization; None if valid."""
    if _VALID_ID_RE.match(secret_id):
        return None
    return f"secret_id '{secret_id}' could not be normalized to a valid identifier."


def make_secure_input_tool(event_bus: "EventBus") -> Any:
    """Create request_secure_input tool bound to the given EventBus."""

    @function_tool(name_override="request_secure_input")
    async def request_secure_input(
        secret_id: str,
        prompt_message: str,
        channel_id: str = "cli_channel",
    ) -> SecureInputResult:
        """Request secure input from user. The secret is saved directly to encrypted storage.

        secret_id is auto-sanitized: dots, dashes, @ etc. are replaced with underscores.
        channel_id: where the user is chatting (e.g. 'cli_channel'). Default: 'cli_channel'.
        """
        secret_id = _sanitize_secret_id(secret_id)
        err = _validate_secret_id(secret_id)
        if err:
            return SecureInputResult(success=False, error=err)
        await event_bus.publish(
            SystemTopics.SECURE_INPUT_REQUEST,
            "kernel",
            {
                "secret_id": secret_id,
                "prompt": prompt_message,
                "target_channel": channel_id,
            },
        )
        return SecureInputResult(
            success=True,
            secret_id=secret_id,
            message=(
                "Secure input request sent to user. "
                f"Secret will be stored as '{secret_id}'. "
                "Wait for system confirmation before proceeding. "
                "Do NOT ask the user for the secret value in this conversation."
            ),
        )

    return request_secure_input
