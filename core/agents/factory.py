"""AgentFactory: creates ephemeral agents from AgentSpec at runtime."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import uuid4

from agents import Agent, Runner

from core.agents.registry import AgentRecord, AgentRegistry
from core.extensions.contract import (
    AgentDescriptor,
    AgentInvocationContext,
    AgentResponse,
)
from core.extensions.manifest import AgentLimits


@dataclass(frozen=True)
class AgentSpec:
    """Four-tuple agent specification (AOrchestra-inspired).

    Instruction, tools, model, and limits define a specialized sub-agent.
    Context curation is deferred; instruction includes task objective.
    """

    name: str
    instruction: str
    description: str = ""
    tools: list[str] = field(default_factory=list)
    model: str | None = None
    max_turns: int = 25
    ttl_seconds: int = 1800


class ToolResolver(Protocol):
    """Protocol for resolving tool IDs to actual tool objects."""

    def __call__(
        self, tool_ids: list[str], agent_id: str | None = None
    ) -> list[Any]: ...


class DynamicAgentProvider:
    """AgentProvider that wraps an ephemeral Agent instance.

    Created by AgentFactory; no manifest, no prompt file.
    """

    def __init__(
        self,
        name: str,
        description: str,
        agent: Agent,
        integration_mode: Literal["tool", "handoff"] = "tool",
        max_turns: int = 25,
    ) -> None:
        self._name = name
        self._description = description
        self._agent = agent
        self._integration_mode = integration_mode
        self._max_turns = max_turns

    def get_agent_descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            name=self._name,
            description=self._description,
            integration_mode=self._integration_mode,
        )

    async def invoke(
        self, task: str, context: AgentInvocationContext | None = None
    ) -> AgentResponse:
        full_task = task
        if context and context.conversation_summary:
            full_task = f"Context:\n{context.conversation_summary}\n\nTask:\n{task}"
        try:
            result = await Runner.run(
                self._agent,
                full_task,
                max_turns=self._max_turns,
            )
            return AgentResponse(
                status="success",
                content=result.final_output or "",
            )
        except Exception as e:
            return AgentResponse(
                status="error",
                content="",
                error=str(e),
            )


class AgentFactory:
    """Creates ephemeral agents from AgentSpec and registers them."""

    def __init__(
        self,
        model_router: Any,
        tool_resolver: ToolResolver,
        registry: AgentRegistry,
    ) -> None:
        self._model_router = model_router
        self._tool_resolver = tool_resolver
        self._registry = registry

    def create(self, spec: AgentSpec) -> str:
        """Create a dynamic agent from spec, register it, return agent_id."""
        agent_id = f"dyn_{uuid4().hex[:12]}"
        tools = self._tool_resolver(spec.tools, agent_id)

        default_cfg = self._model_router.get_default_agent_config()
        if not default_cfg:
            raise ValueError(
                "No default agent config in settings.yaml; cannot create dynamic agent"
            )
        model_name = spec.model or default_cfg.get("model") or ""
        provider_id = default_cfg.get("provider") or ""
        if not model_name:
            raise ValueError("Model not specified and no default model in settings")
        self._model_router.register_agent_config(
            agent_id, {"provider": provider_id, "model": model_name}
        )
        model_instance = self._model_router.get_model(agent_id)

        agent = Agent(
            name=spec.name,
            instructions=spec.instruction,
            model=model_instance,
            tools=tools,
        )
        desc = spec.description or spec.instruction[:200]
        if not spec.description and len(spec.instruction) > 200:
            desc += "..."
        provider = DynamicAgentProvider(
            name=spec.name,
            description=desc,
            agent=agent,
            integration_mode="tool",
            max_turns=spec.max_turns,
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=spec.ttl_seconds)
        limits = AgentLimits(max_turns=spec.max_turns)
        record = AgentRecord(
            id=agent_id,
            name=spec.name,
            description=desc,
            model=model_name,
            integration_mode="tool",
            tools=list(spec.tools),
            limits=limits,
            source="dynamic",
            expires_at=expires_at,
        )
        self._registry.register(record, provider)
        return agent_id
