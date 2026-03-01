"""Channel tools for agent-driven channel selection."""

import json

from typing import Any

from agents import function_tool

from core.extensions.router import MessageRouter


def _make_list_channels(router: MessageRouter) -> Any:
    @function_tool
    async def list_channels() -> str:
        """List all available communication channels.
        Usually unnecessary — available channels are already listed in the system context.
        Use only if channel context is missing or you need to refresh the list.
        Returns JSON array of {channel_id, description} objects for use with send_to_channel."""
        ids = router.get_channel_ids()
        if not ids:
            return json.dumps([], ensure_ascii=False)
        descriptions = router.get_channel_descriptions()
        channels = [{"channel_id": cid, "description": descriptions.get(cid) or ""} for cid in ids]
        return json.dumps(channels, ensure_ascii=False)
    return list_channels


def _make_send_to_channel(router: MessageRouter) -> Any:
    @function_tool
    async def send_to_channel(channel_id: str, text: str) -> str:
        """Send a message to the user via a specific channel.
        channel_id must be one of the available channel IDs from the system context
        (e.g. 'telegram_channel', 'cli_channel').
        Do not call list_channels first — use channel IDs from system context."""
        if channel_id not in router.get_channel_ids():
            return json.dumps(
                {"success": False, "error": f"Channel '{channel_id}' not found. Use list_channels to see available channels."},
                ensure_ascii=False,
            )
        try:
            await router.notify_user(text, channel_id)
            return json.dumps({"success": True}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    return send_to_channel


def make_channel_tools(router: MessageRouter) -> list:
    """Create agent tools for channel discovery and targeted messaging."""
    return [_make_list_channels(router), _make_send_to_channel(router)]
