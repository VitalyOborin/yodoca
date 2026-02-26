"""Heartbeat extension: SchedulerProvider with lightweight Scout agent and escalation."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agents import Agent, ModelSettings, Runner
from pydantic import BaseModel, Field

from core.extensions.instructions import resolve_instructions

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT = (
    "Review the memory context above and check active tasks. "
    "Are there any pending user requests, unfinished tasks, "
    "stalled background work, reminders, or follow-ups that need attention right now?"
)


class TaskSpec(BaseModel):
    """Task specification for submit_task action."""

    goal: str = Field(description="Task description for the agent")
    agent_id: str = Field(default="orchestrator", description="Which agent should handle it")
    priority: int = Field(default=5, description="1-10, higher = more urgent")


class HeartbeatDecision(BaseModel):
    """Structured output from Scout. Required by output_type."""

    action: Literal["noop", "submit_task", "alert"] = Field(...)
    reason: str = Field(default="", description="Brief explanation")
    task: TaskSpec | None = Field(default=None, description="Required when action=submit_task")


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
            context.model_router.get_model(context.agent_id)
            if context.model_router
            else None
        )
        if not model:
            raise RuntimeError("heartbeat: model_router or agent config required")
        self._scout = Agent(
            name="HeartbeatScout",
            instructions=instructions,
            model=model,
            model_settings=ModelSettings(parallel_tool_calls=True),
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
        from core.extensions import TurnContext

        enriched = await self._ctx.enrich_prompt(
            base_prompt,
            turn_context=TurnContext(agent_id=self._ctx.agent_id),
        )

        try:
            result = await Runner.run(self._scout, enriched, max_turns=10)
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
            case "submit_task":
                if not decision.task:
                    logger.warning("heartbeat: submit_task without task spec")
                    return None
                task_engine = self._ctx.get_extension("task_engine")
                if not task_engine:
                    logger.error("heartbeat: task_engine not available")
                    return None
                result = await task_engine.submit_task(
                    goal=decision.task.goal,
                    agent_id=decision.task.agent_id,
                    priority=decision.task.priority,
                )
                logger.info("heartbeat: created task %s -- %s", result.task_id, (decision.reason or "")[:120])
            case "alert":
                logger.info("heartbeat: alert -- %s", (decision.reason or "")[:120])
                return {"text": decision.reason}

        return None
