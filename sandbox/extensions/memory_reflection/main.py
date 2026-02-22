"""Memory Reflection: SchedulerProvider with weekly meta-cognitive reflection agent."""

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import Agent, Runner
from pydantic import BaseModel, Field

from core.extensions.instructions import resolve_instructions

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)

# Idempotency: skip reflection if one was saved within this many seconds (6 days).
_REFLECTION_COOLDOWN_SEC = 6 * 86400


class ReflectionResult(BaseModel):
    """Structured output from weekly reflection. Required by output_type."""

    success: bool = Field(description="True if a meaningful reflection was generated")
    facts_analyzed: int = Field(ge=0, description="Count of facts in the input")
    episodes_analyzed: int = Field(ge=0, description="Count of episodes in the input")
    summary: str = Field(
        default="",
        description="Reflection text or brief reason if success=false",
    )


class MemoryReflectionExtension:
    """SchedulerProvider: weekly meta-cognitive reflection on recent memories."""

    def __init__(self) -> None:
        self._ctx: "ExtensionContext | None" = None
        self._agent: Agent | None = None

    async def initialize(self, context: "ExtensionContext") -> None:
        self._ctx = context
        ext_dir = Path(__file__).resolve().parent
        instructions = resolve_instructions(
            instructions_file="prompt.jinja2",
            extension_dir=ext_dir,
        )
        model = (
            context.model_router.get_model("memory_reflection")
            if context.model_router
            else None
        )
        if not model:
            raise RuntimeError("memory_reflection: model_router or memory_reflection config required")
        self._agent = Agent(
            name="MemoryReflection",
            instructions=instructions or "You are a meta-cognitive analyst.",
            model=model,
            tools=[],
            output_type=ReflectionResult,
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._ctx = None
        self._agent = None

    def health_check(self) -> bool:
        return self._agent is not None

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        """Called by Loader._cron_loop when schedule fires."""
        if task_name != "execute_reflection" or not self._ctx or not self._agent:
            return None

        mem = self._ctx.get_extension("memory")
        if not mem:
            logger.warning("memory extension not available for reflection")
            return None

        # Idempotency: skip if reflection was saved in the last 6 days
        last_ts = await mem.get_latest_reflection_timestamp()
        if last_ts and (time.time() - last_ts) < _REFLECTION_COOLDOWN_SEC:
            logger.info("Reflection skipped: one already exists within 6 days")
            return None

        memories = await mem.get_recent_memories_for_reflection(days=7, limit=200)
        facts = [m for m in memories if m.get("kind") == "fact"]
        episodes = [m for m in memories if m.get("kind") == "episode"]

        if len(facts) < 3:
            logger.info(
                "Reflection skipped: insufficient data (%d facts, %d episodes)",
                len(facts),
                len(episodes),
            )
            return None

        lines = []
        for i, m in enumerate(memories[:100], 1):
            lines.append(f"{i}. [{m['kind']}] {m.get('content', '')}")
        prompt = "Reflect on these recent memories from the last 7 days:\n\n" + "\n".join(
            lines
        )

        try:
            result = await Runner.run(
                self._agent,
                prompt,
                max_turns=1,
            )
            output = result.final_output
            if not isinstance(output, ReflectionResult):
                logger.warning("Reflection agent returned unexpected type: %s", type(output))
                return None

            if not output.success:
                logger.info("Reflection skipped by LLM: %s", output.summary)
                return None

            source_ids = [m["id"] for m in memories[:50]]
            reflection_id = await mem.save_reflection(
                content=output.summary,
                source_ids=source_ids,
                tags=["weekly"],
            )
            logger.info(
                "Reflection saved: id=%s, facts=%d, episodes=%d",
                reflection_id,
                output.facts_analyzed,
                output.episodes_analyzed,
            )
            return {
                "reflection_id": reflection_id,
                "facts_analyzed": output.facts_analyzed,
                "episodes_analyzed": output.episodes_analyzed,
            }
        except Exception as e:
            logger.warning("Reflection failed: %s", e, exc_info=True)
            return None
