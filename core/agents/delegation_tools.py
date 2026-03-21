"""Delegation tools for Orchestrator: list_agents, delegate_task, create_agent."""

from collections.abc import Callable
from typing import Any

from agents import function_tool
from pydantic import BaseModel, Field

from core.agents.factory import AgentFactory, AgentSpec
from core.agents.registry import AgentRecord, AgentRegistry
from core.extensions.contract import AgentInvocationContext
from core.llm.catalog import CapabilityTier, CostTier, ModelCatalog


class AgentInfo(BaseModel):
    """Single agent entry for list_agents result."""

    id: str
    name: str
    description: str
    model: str | None = None
    cost_tier: CostTier | None = None
    capability_tier: CapabilityTier | None = None
    strengths: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    busy: bool = False


class ListAgentsResult(BaseModel):
    """Result of list_agents tool."""

    agents: list[AgentInfo] = Field(default_factory=list)


class DelegateTaskResult(BaseModel):
    """Result of delegate_task tool."""

    success: bool
    agent_id: str
    content: str
    error: str | None = None
    tokens_used: int | None = None


class CreateAgentResult(BaseModel):
    """Result of create_agent tool."""

    success: bool
    agent_id: str
    error: str | None = None
    tools_requested: list[str] = Field(default_factory=list)
    tools_assigned: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ListAvailableToolsResult(BaseModel):
    """Result of list_available_tools tool."""

    tool_ids: list[str] = Field(default_factory=list)
    tool_descriptions: dict[str, str] = Field(default_factory=dict)


class ModelInfoResult(BaseModel):
    """Single model entry for list_models result."""

    id: str
    cost_tier: CostTier
    capability_tier: CapabilityTier
    strengths: list[str] = Field(default_factory=list)
    context_window: int | None = None


class ListModelsResult(BaseModel):
    """Result of list_models tool."""

    models: list[ModelInfoResult] = Field(default_factory=list)


def _normalize_tool_ids(tool_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tool_id in tool_ids:
        key = (tool_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized


def _validate_tool_ids(
    tools: list[str] | None,
    available_tool_ids: list[str],
) -> CreateAgentResult | list[str]:
    """Return normalized tool IDs for create_agent, or an error result."""
    if tools is None:
        return CreateAgentResult(
            success=False,
            agent_id="",
            error=(
                "tools is null. Pass explicit extension IDs in tools, "
                "or pass tools=[] to create an agent without tools."
            ),
            tools_requested=[],
            tools_assigned=[],
            warnings=[],
        )
    requested_tools = _normalize_tool_ids(tools)
    available_set = set(available_tool_ids)
    if requested_tools:
        invalid = [t for t in requested_tools if t not in available_set]
        if invalid:
            available = ", ".join(sorted(available_tool_ids))
            return CreateAgentResult(
                success=False,
                agent_id="",
                error=(
                    "Unknown or unavailable tool IDs: "
                    f"{', '.join(invalid)}. "
                    f"Available: {available}"
                ),
                tools_requested=requested_tools,
                tools_assigned=[],
                warnings=[],
            )
        return requested_tools
    return []


def _record_to_agent_info(
    record: AgentRecord,
    busy: bool,
    catalog: ModelCatalog | None = None,
) -> AgentInfo:
    info = catalog.get_info(record.model) if catalog and record.model else None
    return AgentInfo(
        id=record.id,
        name=record.name,
        description=record.description,
        model=record.model,
        cost_tier=info.cost_tier if info else None,
        capability_tier=info.capability_tier if info else None,
        strengths=list(info.strengths) if info else [],
        tools=list(record.tools),
        busy=busy,
    )


def make_delegation_tools(
    registry: AgentRegistry,
    factory: AgentFactory | None = None,
    get_available_tool_ids: Callable[[], list[str]] | None = None,
    catalog: ModelCatalog | None = None,
    get_tool_catalog: Callable[[], dict[str, dict[str, Any]]] | None = None,
) -> list[Any]:
    """Create list_agents, delegate_task, optionally create_agent and list_models."""

    @function_tool
    async def list_agents(available_only: bool = False) -> ListAgentsResult:
        """List available agents with their capabilities.
        Use to discover which agent is best suited for a task before delegating.
        available_only: if True, only returns agents that are not currently busy."""
        records = registry.list_agents(available_only=available_only)
        infos = [
            _record_to_agent_info(r, registry.is_busy(r.id), catalog) for r in records
        ]
        return ListAgentsResult(agents=infos)

    @function_tool
    async def delegate_task(
        agent_id: str,
        task: str,
        context: str = "",
    ) -> DelegateTaskResult:
        """Delegate a task to a specialized agent by id.
        Use after list_agents to pick the right agent for the job.
        The agent executes the task and returns the result.
        context: optional context to pass to the agent (e.g. conversation summary)."""
        invocation_context: AgentInvocationContext | None = None
        if context:
            invocation_context = AgentInvocationContext(
                conversation_summary=context,
                user_message=None,
                correlation_id=None,
            )
        response = await registry.invoke(agent_id, task, invocation_context)
        if response.status == "success":
            return DelegateTaskResult(
                success=True,
                agent_id=agent_id,
                content=response.content,
                error=None,
                tokens_used=response.tokens_used,
            )
        return DelegateTaskResult(
            success=False,
            agent_id=agent_id,
            content=response.content,
            error=response.error or f"Agent returned status: {response.status}",
            tokens_used=response.tokens_used,
        )

    tools_list: list[Any] = [list_agents, delegate_task]

    if catalog is not None:

        @function_tool
        async def list_models() -> ListModelsResult:
            """List available models with cost and capability metadata.

            Use when choosing a model for create_agent.
            cost_tier: free | low | medium | high
            capability_tier: basic | standard | advanced | frontier
            """
            models = [
                ModelInfoResult(
                    id=m.id,
                    cost_tier=m.cost_tier,
                    capability_tier=m.capability_tier,
                    strengths=list(m.strengths),
                    context_window=m.context_window,
                )
                for m in catalog.list_models()
            ]
            return ListModelsResult(models=models)

        tools_list.append(list_models)

    if factory is not None:

        @function_tool
        async def create_agent(
            name: str,
            instruction: str,
            description: str = "",
            tools: list[str] | None = None,
            model: str | None = None,
            parallel_tool_calls: bool = False,
            max_turns: int = 25,
        ) -> CreateAgentResult:
            """Create a specialized agent on-the-fly for a one-off task.

            Use when no existing agent fits. After creation, use delegate_task
            with the returned agent_id. The agent expires after 30 min.
            description: short human-readable purpose shown in list_agents.
            tools semantics:
            - null: invalid (pass explicit IDs or [])
            - []: explicitly create a no-tools agent
            - [ids...]: explicit extension IDs from list_available_tools (strict)
            model: optional; defaults to default model from settings.
            parallel_tool_calls: allow the model to execute multiple tool calls
            concurrently in a single turn. Defaults to false.
            """
            available_tool_ids = (
                get_available_tool_ids() if get_available_tool_ids is not None else []
            )
            validation = _validate_tool_ids(tools, available_tool_ids)
            if isinstance(validation, CreateAgentResult):
                return validation
            assigned_tools = validation
            requested_tools = assigned_tools
            warnings: list[str] = []

            try:
                spec = AgentSpec(
                    name=name,
                    instruction=instruction,
                    description=description,
                    tools=assigned_tools,
                    model=model,
                    parallel_tool_calls=parallel_tool_calls,
                    max_turns=max_turns,
                )
                agent_id = factory.create(spec)
                return CreateAgentResult(
                    success=True,
                    agent_id=agent_id,
                    error=None,
                    tools_requested=requested_tools,
                    tools_assigned=assigned_tools,
                    warnings=warnings,
                )
            except Exception as e:
                return CreateAgentResult(
                    success=False,
                    agent_id="",
                    error=str(e),
                    tools_requested=requested_tools,
                    tools_assigned=assigned_tools,
                    warnings=warnings,
                )

        tools_list.append(create_agent)

    if get_available_tool_ids is not None:

        @function_tool
        async def list_available_tools() -> ListAvailableToolsResult:
            """List tool IDs usable when creating a dynamic agent.

            Use with create_agent:
            - pass tool_ids as create_agent.tools values
            - use descriptions for tool selection
            """
            tool_ids = get_available_tool_ids()
            descriptions: dict[str, str] = {}
            if get_tool_catalog is not None:
                catalog_data = get_tool_catalog()
                for tool_id in tool_ids:
                    meta = catalog_data.get(tool_id, {})
                    descriptions[tool_id] = str(meta.get("description", "") or "")
            return ListAvailableToolsResult(
                tool_ids=tool_ids,
                tool_descriptions=descriptions,
            )

        tools_list.append(list_available_tools)

    return tools_list
