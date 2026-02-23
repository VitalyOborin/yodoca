"""Memory write-path agent. Phase 3."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import Agent, Runner

from core.extensions.instructions import resolve_instructions

logger = logging.getLogger(__name__)


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
        *,
        causal_instructions: str | None = None,
    ) -> None:
        self._agent = Agent(
            name="MemoryWritePathAgent",
            instructions=instructions,
            model=model,
            tools=tools,
        )
        self._causal_agent: Agent | None = None
        if causal_instructions:
            self._causal_agent = Agent(
                name="MemoryCausalAgent",
                instructions=causal_instructions,
                model=model,
                tools=tools,
            )

    async def consolidate_session(self, session_id: str) -> ConsolidationResult:
        """Run consolidation for a session. Agent uses tools to extract, link, and mark done."""
        task = f"Consolidate session {session_id}. Follow the workflow: check idempotency, fetch episodes, extract facts/procedures/opinions, save nodes with derived_from edges, link entities, resolve conflicts if any, then mark session consolidated."
        logger.info("Consolidation started: session=%s", session_id)
        try:
            await Runner.run(
                self._agent,
                task,
                max_turns=10,
            )
            logger.info("Consolidation completed: session=%s", session_id)
            return ConsolidationResult(
                session_id=session_id,
                status="completed",
            )
        except Exception:
            logger.exception("Consolidation error: session=%s", session_id)
            return ConsolidationResult(
                session_id=session_id,
                status="error",
            )

    async def enrich_entity(
        self,
        entity_id: str,
        entity_name: str,
        entity_type: str,
        related_contents: list[str],
    ) -> bool:
        """Generate and save entity summary from related node contents. Returns True if successful."""
        if not related_contents:
            return False
        context = "\n".join(f"- {c[:300]}" for c in related_contents[:10])
        task = (
            f"Generate a concise 1-3 sentence summary for entity '{entity_name}' (type: {entity_type}) "
            f"based on these related facts:\n{context}\n\n"
            f"Call update_entity_summary with entity_id='{entity_id}' and your generated summary."
        )
        logger.info("Enriching entity: %s (%s), %d contents", entity_name, entity_type, len(related_contents))
        try:
            await Runner.run(self._agent, task, max_turns=3)
            logger.info("Entity enriched: %s", entity_name)
            return True
        except Exception:
            logger.exception("Entity enrichment failed: %s", entity_name)
            return False

    async def infer_causal_edges(
        self, episode_pairs: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> int:
        """Analyze episode pairs for causal relationships. Returns count of pairs analyzed."""
        if not self._causal_agent or not episode_pairs:
            return 0
        lines = []
        for i, (prev, curr) in enumerate(episode_pairs[:20], 1):
            lines.append(
                f"Pair {i}: Episode A (id={prev['id']}): \"{prev.get('content', '')[:200]}\" "
                f"-> Episode B (id={curr['id']}): \"{curr.get('content', '')[:200]}\""
            )
        task = (
            "Analyze these consecutive episode pairs. For each pair where A clearly caused B, "
            "call save_causal_edges with source_id=Episode A id, target_id=Episode B id.\n\n"
            + "\n".join(lines)
        )
        logger.info("Causal inference started: %d pairs", len(episode_pairs))
        try:
            await Runner.run(self._causal_agent, task, max_turns=5)
            logger.info("Causal inference completed: %d pairs analyzed", len(episode_pairs))
            return len(episode_pairs)
        except Exception:
            logger.exception("Causal inference failed")
            return 0


def create_memory_agent(
    model: Any,
    tools: list[Any],
    extension_dir: Path,
) -> MemoryAgent:
    """Create MemoryAgent with consolidation and causal inference instructions."""
    consolidation_instructions = resolve_instructions(
        instructions_file="prompt.jinja2",
        extension_dir=extension_dir,
        template_vars={
            "sandbox_dir": str(extension_dir.parent),
            "mode": "consolidation",
        },
    )
    causal_instructions = resolve_instructions(
        instructions_file="prompt.jinja2",
        extension_dir=extension_dir,
        template_vars={
            "sandbox_dir": str(extension_dir.parent),
            "mode": "causal_inference",
        },
    )
    return MemoryAgent(
        model=model,
        tools=tools,
        instructions=consolidation_instructions,
        causal_instructions=causal_instructions,
    )
