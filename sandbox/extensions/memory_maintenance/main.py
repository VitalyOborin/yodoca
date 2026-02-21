"""Memory Maintenance: AgentProvider + SchedulerProvider.

Responsibilities:
- Consolidation: extract semantic facts from completed sessions (LLM).
- Decay + Prune: apply Ebbinghaus decay, soft-delete stale facts (planned).
- Reflection: weekly meta-cognitive summary of recent facts/episodes (LLM).
"""

import logging
import time
from pathlib import Path
from typing import Any

import yaml
from agents import Agent, Runner
from pydantic import BaseModel, Field

from core.extensions.contract import (
    AgentDescriptor,
    AgentInvocationContext,
    AgentProvider,
    AgentResponse,
    SchedulerProvider,
)
from core.extensions.instructions import resolve_instructions
from core.extensions.manifest import ExtensionManifest

logger = logging.getLogger(__name__)

# Idempotency: skip reflection if one was saved within this many seconds (6 days).
_REFLECTION_COOLDOWN_SEC = 6 * 86400


class ConsolidationResult(BaseModel):
    """Structured output from memory consolidation. Required by output_type."""

    session_id: str = Field(description="Session that was consolidated")
    success: bool = Field(description="Whether consolidation completed successfully")
    facts_extracted: int = Field(
        ge=0,
        description="Number of facts saved to memory",
    )
    conflicts_resolved: int = Field(
        default=0,
        ge=0,
        description="Contradictions detected and resolved",
    )
    skipped: bool = Field(
        default=False,
        description="True if session was already consolidated (no work done)",
    )
    summary: str = Field(
        default="",
        description="Brief human-readable summary of what was extracted or why skipped",
    )


class ReflectionResult(BaseModel):
    """Structured output from weekly reflection. Required by output_type."""

    success: bool = Field(description="True if a meaningful reflection was generated")
    facts_analyzed: int = Field(ge=0, description="Count of facts in the input")
    episodes_analyzed: int = Field(ge=0, description="Count of episodes in the input")
    summary: str = Field(
        default="",
        description="Reflection text or brief reason if success=false",
    )


class MemoryMaintenanceExtension:
    """AgentProvider + SchedulerProvider: memory consolidation, decay, and pruning."""

    def __init__(self) -> None:
        self._agent: Agent | None = None
        self._reflection_agent: Agent | None = None
        self._manifest: ExtensionManifest | None = None
        self._ctx: Any = None

    # --- AgentProvider ---

    def get_agent_descriptor(self) -> AgentDescriptor:
        assert self._manifest is not None
        return AgentDescriptor(
            name=self._manifest.name,
            description=self._manifest.description,
            integration_mode=self._manifest.agent.integration_mode,
        )

    async def invoke(
        self, task: str, context: AgentInvocationContext | None = None
    ) -> AgentResponse:
        if not self._agent:
            return AgentResponse(
                status="error",
                content="",
                error="Agent not initialized",
            )
        try:
            result = await Runner.run(
                self._agent,
                task,
                max_turns=self._manifest.agent.limits.max_turns,
            )
            output = result.final_output
            if isinstance(output, ConsolidationResult):
                content = output.model_dump_json()
            else:
                content = str(output) if output else ""
            return AgentResponse(
                status="success",
                content=content,
            )
        except Exception as e:
            return AgentResponse(
                status="error",
                content="",
                error=str(e),
            )

    # --- SchedulerProvider ---

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        match task_name:
            case "execute_consolidation":
                return await self._run_consolidation()
            case "execute_decay":
                return await self._run_decay_and_prune()
            case "execute_reflection":
                return await self._run_reflection()
            case _:
                logger.warning("Unknown scheduled task: %s", task_name)
                return None

    # --- Scheduled task implementations ---

    async def _run_consolidation(self) -> dict[str, Any] | None:
        """Emit memory.session_completed for all pending sessions."""
        mem = self._ctx.get_extension("memory")
        if not mem:
            logger.warning("memory extension not available for consolidation")
            return None
        pending = await mem.get_all_pending_consolidations()
        for session_id in pending:
            await self._ctx.emit(
                "memory.session_completed",
                {
                    "session_id": session_id,
                    "prompt": f"Consolidate session {session_id}: extract semantic facts.",
                },
            )
        logger.info("Consolidation triggered for %d pending sessions", len(pending))
        return None

    async def _run_decay_and_prune(self) -> dict[str, Any] | None:
        """Apply Ebbinghaus decay; soft-delete facts below threshold."""
        mem = self._ctx.get_extension("memory")
        if not mem:
            logger.warning("memory extension not available for decay")
            return None

        threshold = self._ctx.get_config("decay_threshold", 0.05)
        stats = await mem.run_decay_and_prune(threshold)

        logger.info(
            "Decay complete: %d facts updated, %d facts pruned (threshold=%.2f)",
            stats["decayed"],
            stats["pruned"],
            threshold,
        )

        if stats["errors"]:
            logger.warning("Decay errors: %s", stats["errors"])

        return None  # No user notification for background maintenance

    async def _run_reflection(self) -> dict[str, Any] | None:
        """Generate weekly meta-cognitive reflection from recent facts/episodes."""
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

        if not self._reflection_agent:
            logger.warning("Reflection agent not initialized")
            return None

        lines = []
        for i, m in enumerate(memories[:100], 1):
            lines.append(f"{i}. [{m['kind']}] {m.get('content', '')}")
        prompt = "Reflect on these recent memories from the last 7 days:\n\n" + "\n".join(
            lines
        )

        try:
            result = await Runner.run(
                self._reflection_agent,
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

    # --- Lifecycle ---

    async def initialize(self, context: Any) -> None:
        self._ctx = context
        ext_dir = Path(__file__).resolve().parent
        manifest_path = ext_dir / "manifest.yaml"
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self._manifest = ExtensionManifest.model_validate(data)
        mem = context.get_extension("memory")
        if not mem:
            raise RuntimeError("memory extension not found")
        tools = mem.get_consolidator_tools()
        model = (
            context.model_router.get_model(context.agent_id)
            if context.model_router
            else context.agent_model
        )
        self._agent = Agent(
            name=self._manifest.name,
            instructions=context.resolved_instructions,
            model=model,
            tools=tools,
            output_type=ConsolidationResult,
        )

        # Reflection agent: no tools, single LLM call, structured output
        project_root = ext_dir.parent.parent.parent  # sandbox/extensions/<id> -> project root
        reflection_instructions = resolve_instructions(
            instructions_file="prompts/memory_reflection.jinja2",
            extension_dir=ext_dir,
            project_root=project_root,
        )
        self._reflection_agent = Agent(
            name=f"{self._manifest.name} (reflection)",
            instructions=reflection_instructions,
            model=model,
            tools=[],
            output_type=ReflectionResult,
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._agent = None
        self._reflection_agent = None
        self._manifest = None
        self._ctx = None

    def health_check(self) -> bool:
        return self._agent is not None
