"""ResponseDeliveryService: channel-specific response delivery logic."""

from core.extensions.agent_invoker import AgentInvoker
from core.extensions.contract import (
    ChannelProvider,
    StreamingChannelProvider,
    TurnContext,
)


class ResponseDeliveryService:
    """Delivers responses to channels using streaming when supported."""

    def __init__(self, invoker: AgentInvoker) -> None:
        self._invoker = invoker

    async def deliver(
        self,
        channel: ChannelProvider,
        user_id: str,
        text: str,
        turn_context: TurnContext,
    ) -> str:
        if isinstance(channel, StreamingChannelProvider):
            async def _on_chunk(chunk: str) -> None:
                await channel.on_stream_chunk(user_id, chunk)

            async def _on_tool_call(name: str) -> None:
                await channel.on_stream_status(user_id, f"Using: {name}")

            await channel.on_stream_start(user_id)
            response = await self._invoker.invoke_agent_streamed(
                text,
                on_chunk=_on_chunk,
                on_tool_call=_on_tool_call,
                turn_context=turn_context,
            )
            await channel.on_stream_end(user_id, response)
        else:
            response = await self._invoker.invoke_agent(text, turn_context)
            await channel.send_to_user(user_id, response)
        return response
