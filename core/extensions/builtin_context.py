"""Built-in ContextProvider: injects current channel identity and available channels into the system prompt."""

from core.extensions.contract import TurnContext
from core.extensions.router import MessageRouter


class ActiveChannelContextProvider:
    """Built-in ContextProvider that injects current channel identity and available channels into the system prompt."""

    def __init__(self, router: MessageRouter) -> None:
        self._router = router

    @property
    def context_priority(self) -> int:
        return 0

    def _build_available_channels_section(self) -> str:
        """Build [Available Channels] section listing all registered channels with readiness."""
        ids = self._router.get_channel_ids()
        if not ids:
            return ""
        descriptions = self._router.get_channel_descriptions()
        lines: list[str] = []
        for cid in ids:
            desc = descriptions.get(cid) or cid
            ch = self._router.get_channel(cid)
            ready = getattr(ch, "health_check", lambda: True)()
            status = "READY" if ready else "NOT CONFIGURED"
            lines.append(f"- {cid} ({desc}) — {status}")
        return (
            "[Available Channels]\n"
            + "\n".join(lines)
            + "\n\nUse send_to_channel(channel_id=\"...\", text=\"...\") directly. "
            "Do not call list_channels — use channel IDs from this list."
        )

    async def get_context(self, prompt: str, turn_context: TurnContext) -> str | None:
        parts: list[str] = []

        # Always inject available channels
        channels_section = self._build_available_channels_section()
        if channels_section:
            parts.append(channels_section)

        # When user is on a specific channel, add session context
        if turn_context.channel_id:
            descriptions = self._router.get_channel_descriptions()
            channel_desc = (
                descriptions.get(turn_context.channel_id) or turn_context.channel_id
            )
            user_id = turn_context.user_id or "unknown"
            parts.append(
                "[Current Session Context]\n"
                f"Channel: {turn_context.channel_id} ({channel_desc})\n"
                f"User ID: {user_id}\n\n"
                f"IMPORTANT: You are currently communicating with the user through the '{turn_context.channel_id}'. "
                "Any actions, tool calls, or notifications should assume this channel unless the user explicitly requests otherwise. "
                "Do not ask the user to switch channels if they are already here."
            )

        if not parts:
            return None
        return "\n\n---\n\n".join(parts)
