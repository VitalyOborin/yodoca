"""Memory Maintenance: AgentProvider + SchedulerProvider.

Responsibilities:
- Consolidation: extract semantic facts from completed sessions (LLM).
- Decay + Prune: apply Ebbinghaus decay, soft-delete stale facts (planned).
"""

import logging
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
from core.extensions.manifest import ExtensionManifest

logger = logging.getLogger(__name__)


class ConsolidationResult(BaseModel):
    """Structured output from memory consolidation. Required by output_type."""

    session_id: str = Field(description="Session that was consolidated")
    success: bool = Field(description="Whether consolidation completed successfully")
    facts_extracted: int = Field(
        ge=0,
        description="Number of facts saved to memory",
    )
    skipped: bool = Field(
        default=False,
        description="True if session was already consolidated (no work done)",
    )
    summary: str = Field(
        default="",
        description="Brief human-readable summary of what was extracted or why skipped",
    )


class MemoryMaintenanceExtension:
    """AgentProvider + SchedulerProvider: memory consolidation, decay, and pruning."""

    def __init__(self) -> None:
        self._agent: Agent | None = None
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

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        self._agent = None
        self._manifest = None
        self._ctx = None

    def health_check(self) -> bool:
        return self._agent is not None
