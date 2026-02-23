"""Memory write-path agent. Phase 3."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import Agent, Runner

from core.extensions.instructions import resolve_instructions


@dataclass
class ConsolidationResult:
    """Result of consolidate_session."""

    session_id: str
    facts_extracted: int = 0
    entities_linked: int = 0
    conflicts_resolved: int = 0
    status: str = "completed"


class MemoryAgent:
    """LLM-powered consolidation agent. Extracts facts from episodes, links entities, resolves conflicts."""

    def __init__(
        self,
        model: Any,
        tools: list[Any],
        instructions: str,
    ) -> None:
        self._agent = Agent(
            name="MemoryWritePathAgent",
            instructions=instructions,
            model=model,
            tools=tools,
        )

    async def consolidate_session(self, session_id: str) -> ConsolidationResult:
        """Run consolidation for a session. Agent uses tools to extract, link, and mark done."""
        task = f"Consolidate session {session_id}. Follow the workflow: check idempotency, fetch episodes, extract facts/procedures/opinions, save nodes with derived_from edges, link entities, resolve conflicts if any, then mark session consolidated."
        try:
            await Runner.run(
                self._agent,
                task,
                max_turns=10,
            )
            return ConsolidationResult(
                session_id=session_id,
                status="completed",
            )
        except Exception:
            return ConsolidationResult(
                session_id=session_id,
                status="error",
            )


def create_memory_agent(
    model: Any,
    tools: list[Any],
    extension_dir: Path,
) -> MemoryAgent:
    """Create MemoryAgent with instructions from prompt.jinja2."""
    instructions = resolve_instructions(
        instructions_file="prompt.jinja2",
        extension_dir=extension_dir,
        template_vars={"sandbox_dir": str(extension_dir.parent)},
    )
    return MemoryAgent(model=model, tools=tools, instructions=instructions)
