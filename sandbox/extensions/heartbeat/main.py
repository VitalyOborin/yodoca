"""Heartbeat extension: SchedulerProvider with lightweight Scout agent and escalation."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agents import Agent, Runner
from pydantic import BaseModel, Field

from core.extensions.instructions import resolve_instructions

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT = (
    "Review the memory context above. Are there any pending user requests, "
    "unfinished tasks, reminders, or follow-ups that need attention right now?"
)


class HeartbeatDecision(BaseModel):
    """Structured output from Scout. Required by output_type."""

    action: Literal["noop", "done", "escalate"] = Field(
        description="noop: nothing to do. done: task handled. escalate: needs orchestrator."
    )
    reason: str = Field(
        default="",
        description="Brief explanation. For escalate: task description for orchestrator.",
    )


class HeartbeatExtension:
    """SchedulerProvider: Scout agent checks memory, escalates to Orchestrator when needed."""

    def __init__(self) -> None:
        self._ctx: "ExtensionContext | None" = None
        self._scout: Agent | None = None

    async def initialize(self, context: "ExtensionContext") -> None:
        self._ctx = context
        ext_dir = Path(__file__).resolve().parent
        instructions = resolve_instructions(
            instructions_file="prompt.jinja2",
            extension_dir=ext_dir,
        )
        model = (
            context.model_router.get_model("heartbeat_scout")
            if context.model_router
            else None
        )
        if not model:
            raise RuntimeError("heartbeat: model_router or heartbeat_scout config required")
        self._scout = Agent(
            name="HeartbeatScout",
            instructions=instructions,
            model=model,
            tools=context.resolved_tools,
            output_type=HeartbeatDecision,
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._ctx = None
        self._scout = None

    def health_check(self) -> bool:
        return self._scout is not None

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        """Called by Loader._cron_loop when schedule fires."""
        if task_name != "emit_heartbeat" or not self._ctx or not self._scout:
            return None

        base_prompt = str(self._ctx.get_config("prompt", _DEFAULT_PROMPT)).strip()
        enriched = await self._ctx.enrich_prompt(base_prompt, agent_id="heartbeat_scout")

        try:
            result = await Runner.run(self._scout, enriched, max_turns=1)
            decision: HeartbeatDecision = result.final_output
        except Exception as e:
            logger.warning("heartbeat scout failed: %s", e)
            return None

        if not isinstance(decision, HeartbeatDecision):
            logger.warning("heartbeat: unexpected output type %s", type(decision))
            return None

        match decision.action:
            case "noop":
                logger.debug("heartbeat: noop")
            case "done":
                logger.info("heartbeat: done — %s", (decision.reason or "")[:120])
            case "escalate":
                logger.info("heartbeat: escalate → %s", (decision.reason or "")[:120])
                await self._ctx.request_agent_task(decision.reason)

        return None
