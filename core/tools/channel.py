"""Channel tools for agent-driven channel selection."""

from typing import Any

from agents import function_tool
from pydantic import BaseModel, Field

from core.extensions.router import MessageRouter


class ChannelInfo(BaseModel):
    """Single channel entry for list_channels."""

    channel_id: str
    description: str = ""


class ListChannelsResult(BaseModel):
    """Result of list_channels tool."""

    success: bool
    channels: list[ChannelInfo] = Field(default_factory=list)
    error: str | None = None


class SendToChannelResult(BaseModel):
    """Result of send_to_channel tool."""

    success: bool
    error: str | None = None


def _make_list_channels(router: MessageRouter) -> Any:
    @function_tool
    async def list_channels() -> ListChannelsResult:
        """List all available communication channels.
        Usually unnecessary — channels are listed in system context.
        Use only if channel context is missing or you need to refresh."""
        ids = router.get_channel_ids()
        if not ids:
            return ListChannelsResult(success=True, channels=[])
        descriptions = router.get_channel_descriptions()
        channels = [
            ChannelInfo(channel_id=cid, description=descriptions.get(cid) or "")
            for cid in ids
        ]
        return ListChannelsResult(success=True, channels=channels)

    return list_channels


def _make_send_to_channel(router: MessageRouter) -> Any:
    @function_tool
    async def send_to_channel(channel_id: str, text: str) -> SendToChannelResult:
        """Send a message to the user via a specific channel.
        channel_id must be one of the available channel IDs from the system context
        (e.g. 'telegram_channel', 'cli_channel').
        Do not call list_channels first — use channel IDs from system context."""
        if channel_id not in router.get_channel_ids():
            return SendToChannelResult(
                success=False,
                error=f"Channel '{channel_id}' not found. Use list_channels.",
            )
        try:
            await router.notify_user(text, channel_id)
            return SendToChannelResult(success=True)
        except Exception as e:
            return SendToChannelResult(success=False, error=str(e))

    return send_to_channel


def make_channel_tools(router: MessageRouter) -> list:
    """Create agent tools for channel discovery and targeted messaging."""
    return [_make_list_channels(router), _make_send_to_channel(router)]
