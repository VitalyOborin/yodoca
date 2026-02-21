"""Memory Consolidator: AgentProvider + SchedulerProvider. Extracts facts from completed sessions.

Uses structured output (output_type) per OpenAI Agents SDK:
https://openai.github.io/openai-agents-python/agents/#output-types
"""

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


class MemoryConsolidatorExtension:
    """AgentProvider + SchedulerProvider: LLM-based fact extraction from sessions."""

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
    def get_schedule(self) -> str:
        return "0 3 * * *"  # 03:00 daily

    async def execute(self) -> dict[str, Any] | None:
        mem = self._ctx.get_extension("memory")
        if not mem:
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
