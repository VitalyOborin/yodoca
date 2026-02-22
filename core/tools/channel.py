"""Channel tools for agent-driven channel selection."""

from agents import function_tool

from core.extensions.router import MessageRouter


def make_channel_tools(router: MessageRouter) -> list:
    """Create agent tools for channel discovery and targeted messaging."""

    @function_tool
    async def list_channels() -> str:
        """List all available communication channels.
        Returns channel IDs the agent can use with send_to_channel."""
        ids = router.get_channel_ids()
        if not ids:
            return "No channels registered."
        descriptions = router.get_channel_descriptions()
        parts = []
        for cid in ids:
            label = descriptions.get(cid)
            parts.append(f"{cid} ({label})" if label else cid)
        return ", ".join(parts)

    @function_tool
    async def send_to_channel(channel_id: str, text: str) -> str:
        """Send a message to the user via a specific channel.

        Use when the user explicitly asks to communicate through a particular channel
        (e.g. "send to Telegram", "напиши мне в Slack").

        Args:
            channel_id: Channel ID from list_channels (e.g. "telegram_channel").
            text: Message to deliver.
        """
        if channel_id not in router.get_channel_ids():
            return f"Error: channel '{channel_id}' not found. Use list_channels to see available channels."
        await router.notify_user(text, channel_id)
        return f"Message sent to {channel_id}."

    return [list_channels, send_to_channel]
