"""Tool to request secure input from user via channel interceptor. Secret never reaches LLM."""

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from agents import function_tool
from pydantic import BaseModel

from core.events.topics import SystemTopics

if TYPE_CHECKING:
    from core.events.bus import EventBus

_SECRET_ID_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


class SecureInputResult(BaseModel):
    """Result of request_secure_input tool."""

    success: bool
    message: str = ""
    error: str | None = None


def _validate_secret_id(secret_id: str) -> str | None:
    """Return error message if invalid; None if valid."""
    if _SECRET_ID_PATTERN.match(secret_id):
        return None
    return f"invalid secret_id '{secret_id}'. Use alphanumeric and underscores only."


def make_secure_input_tool(
    event_bus: "EventBus",
) -> Callable[..., Awaitable[SecureInputResult]]:
    """Create request_secure_input tool bound to the given EventBus."""

    @function_tool(name_override="request_secure_input")
    async def request_secure_input(
        secret_id: str,
        prompt_message: str,
        channel_id: str = "cli_channel",
    ) -> SecureInputResult:
        """Request secure input from user. The secret is saved directly to encrypted storage.
        channel_id: where the user is chatting (e.g. 'cli_channel'). Default: 'cli_channel'."""
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
            message=(
                "Secure input request sent to user. "
                "Wait for system confirmation before proceeding. "
                "Do NOT ask the user for the secret value in this conversation."
            ),
        )

    return request_secure_input
