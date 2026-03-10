"""ApprovalCoordinator: MCP approval interruption flow management."""

import asyncio
import logging
import uuid
from typing import Any

from core.events.topics import SystemTopics

logger = logging.getLogger(__name__)


class ApprovalCoordinator:
    """Handles MCP tool approval requests/responses and re-run loops."""

    def __init__(self, approval_timeout: float = 60.0) -> None:
        self._event_bus: Any = None
        self._pending: dict[str, tuple[asyncio.Event, dict[str, Any]]] = {}
        self._approval_timeout = approval_timeout

    def bind_event_bus(self, event_bus: Any) -> None:
        self._event_bus = event_bus
        if event_bus:
            event_bus.subscribe(
                SystemTopics.MCP_TOOL_APPROVAL_RESPONSE,
                self.on_approval_response,
                "kernel.router",
            )

    async def on_approval_response(self, event: Any) -> None:
        payload = event.payload if hasattr(event, "payload") else {}
        request_id = payload.get("request_id")
        if not request_id:
            return
        pending = self._pending.pop(request_id, None)
        if pending:
            wait_event, result = pending
            result["approved"] = payload.get("approved", False)
            result["reason"] = payload.get("reason")
            wait_event.set()

    async def _handle_one_interruption(
        self,
        item: Any,
        channel_id: str | None,
        state: Any,
    ) -> None:
        request_id, wait_event = str(uuid.uuid4()), asyncio.Event()
        result_holder: dict[str, Any] = {}
        self._pending[request_id] = (wait_event, result_holder)
        tool_name = getattr(item, "tool_name", None) or getattr(item, "name", "?")
        args_str = str(getattr(item, "arguments", ""))
        try:
            if self._event_bus:
                await self._event_bus.publish(
                    SystemTopics.MCP_TOOL_APPROVAL_REQUEST,
                    "kernel.router",
                    {
                        "request_id": request_id,
                        "tool_name": tool_name,
                        "arguments": args_str,
                        "server_alias": "",
                        "channel_id": channel_id,
                    },
                )
                try:
                    await asyncio.wait_for(
                        wait_event.wait(),
                        timeout=self._approval_timeout,
                    )
                except TimeoutError:
                    logger.warning(
                        "MCP tool approval timed out for %s, rejecting",
                        tool_name,
                    )
            else:
                result_holder["approved"] = False

            if result_holder.get("approved", False):
                state.approve(item)
            else:
                state.reject(item, always_reject=True)
        finally:
            self._pending.pop(request_id, None)

    async def run_with_approval_loop(
        self,
        agent: Any,
        input_or_state: str | Any,
        session: Any,
        channel_id: str | None,
        max_rounds: int = 10,
    ) -> Any:
        from agents import Runner

        result = await Runner.run(agent, input_or_state, session=session)
        rounds = 0
        while rounds < max_rounds:
            interruptions = getattr(result, "interruptions", None)
            if not isinstance(interruptions, list | tuple) or not interruptions:
                break
            rounds += 1
            state = result.to_state()
            for item in interruptions:
                await self._handle_one_interruption(item, channel_id, state)
            result = await Runner.run(agent, state, session=session)
        return result
