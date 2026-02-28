"""Channel tools for agent-driven channel selection."""

import json

from agents import function_tool

from core.extensions.router import MessageRouter


def make_channel_tools(router: MessageRouter) -> list:
    """Create agent tools for channel discovery and targeted messaging."""

    @function_tool
    async def list_channels() -> str:
        """List all available communication channels.
        Returns JSON array of {channel_id, description} objects for use with send_to_channel."""
        ids = router.get_channel_ids()
        if not ids:
            return json.dumps([], ensure_ascii=False)
        descriptions = router.get_channel_descriptions()
        channels = [
            {"channel_id": cid, "description": descriptions.get(cid) or ""}
            for cid in ids
        ]
        return json.dumps(channels, ensure_ascii=False)

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
            return json.dumps(
                {
                    "success": False,
                    "error": f"Channel '{channel_id}' not found. Use list_channels to see available channels.",
                },
                ensure_ascii=False,
            )
        try:
            await router.notify_user(text, channel_id)
            return json.dumps({"success": True}, ensure_ascii=False)
        except Exception as e:
            return json.dumps(
                {"success": False, "error": str(e)},
                ensure_ascii=False,
            )

    return [list_channels, send_to_channel]
