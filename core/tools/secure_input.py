"""Tool to request secure input from user via channel interceptor. Secret never reaches LLM."""

import re

from agents import function_tool

from core.events.topics import SystemTopics

_SECRET_ID_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def make_secure_input_tool(event_bus):
    """Create request_secure_input tool bound to the given EventBus."""

    @function_tool(name_override="request_secure_input")
    async def request_secure_input(
        secret_id: str,
        prompt_message: str,
        channel_id: str = "cli_channel",
    ) -> str:
        """Request secure input from user. The secret is saved directly to
        encrypted storage. The value never appears in this conversation.

        Args:
            secret_id: Identifier for the secret (e.g. telegram_token).
            prompt_message: Message shown to the user.
            channel_id: Channel to collect input from (default: cli_channel).
        """
        if not _SECRET_ID_PATTERN.match(secret_id):
            return (
                f"Error: invalid secret_id '{secret_id}'. "
                "Use alphanumeric and underscores only."
            )
        await event_bus.publish(
            SystemTopics.SECURE_INPUT_REQUEST,
            "kernel",
            {
                "secret_id": secret_id,
                "prompt": prompt_message,
                "target_channel": channel_id,
            },
        )
        return (
            "Secure input request sent to user. "
            "Wait for system confirmation before proceeding. "
            "Do NOT ask the user for the secret value in this conversation."
        )

    return request_secure_input
