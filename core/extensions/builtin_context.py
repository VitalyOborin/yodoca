"""Built-in ContextProvider: injects current channel identity into the system prompt."""

from core.extensions.contract import TurnContext
from core.extensions.router import MessageRouter


class ActiveChannelContextProvider:
    """Built-in ContextProvider that injects current channel identity into the system prompt."""

    def __init__(self, router: MessageRouter) -> None:
        self._router = router

    @property
    def context_priority(self) -> int:
        return 0

    async def get_context(self, prompt: str, turn_context: TurnContext) -> str | None:
        if not turn_context.channel_id:
            return None
        descriptions = self._router.get_channel_descriptions()
        channel_desc = (
            descriptions.get(turn_context.channel_id) or turn_context.channel_id
        )
        user_id = turn_context.user_id or "unknown"
        return (
            "[Current Session Context]\n"
            f"Channel: {turn_context.channel_id} ({channel_desc})\n"
            f"User ID: {user_id}\n\n"
            f"IMPORTANT: You are currently communicating with the user through the '{turn_context.channel_id}'. "
            "Any actions, tool calls, or notifications should assume this channel unless the user explicitly requests otherwise. "
            "Do not ask the user to switch channels if they are already here."
        )
