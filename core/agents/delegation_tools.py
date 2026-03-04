"""Delegation tools for Orchestrator: list_agents, delegate_task, create_agent."""

from collections.abc import Callable
from typing import Any

from agents import function_tool
from pydantic import BaseModel, Field

from core.agents.factory import AgentFactory, AgentSpec
from core.agents.registry import AgentRecord, AgentRegistry
from core.extensions.contract import AgentInvocationContext


class AgentInfo(BaseModel):
    """Single agent entry for list_agents result."""

    id: str
    name: str
    description: str
    model: str | None = None
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


class ListAvailableToolsResult(BaseModel):
    """Result of list_available_tools tool."""

    tool_ids: list[str] = Field(default_factory=list)


def _record_to_agent_info(record: AgentRecord, busy: bool) -> AgentInfo:
    return AgentInfo(
        id=record.id,
        name=record.name,
        description=record.description,
        model=record.model,
        tools=list(record.tools),
        busy=busy,
    )


def make_delegation_tools(
    registry: AgentRegistry,
    factory: AgentFactory | None = None,
    get_available_tool_ids: Callable[[], list[str]] | None = None,
) -> list[Any]:
    """Create list_agents, delegate_task, and optionally create_agent tools."""

    @function_tool
    async def list_agents(available_only: bool = False) -> ListAgentsResult:
        """List available agents with their capabilities.
        Use to discover which agent is best suited for a task before delegating.
        available_only: if True, only returns agents that are not currently busy."""
        records = registry.list_agents(available_only=available_only)
        infos = [
            _record_to_agent_info(r, registry.is_busy(r.id))
            for r in records
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

    if factory is not None:

        @function_tool
        async def create_agent(
            name: str,
            instruction: str,
            description: str = "",
            tools: list[str] | None = None,
            model: str | None = None,
            max_turns: int = 25,
        ) -> CreateAgentResult:
            """Create a specialized agent on-the-fly for a one-off task.

            Use when no existing agent fits. After creation, use delegate_task
            with the returned agent_id. The agent expires after 30 min.
            description: short human-readable purpose shown in list_agents.
            tools: extension IDs from list_available_tools.
            model: optional; defaults to default model from settings.
            """
            tool_list = tools or []
            try:
                spec = AgentSpec(
                    name=name,
                    instruction=instruction,
                    description=description,
                    tools=tool_list,
                    model=model,
                    max_turns=max_turns,
                )
                agent_id = factory.create(spec)
                return CreateAgentResult(success=True, agent_id=agent_id, error=None)
            except Exception as e:
                return CreateAgentResult(
                    success=False, agent_id="", error=str(e)
                )

        tools_list.append(create_agent)

    if get_available_tool_ids is not None:

        @function_tool
        async def list_available_tools() -> ListAvailableToolsResult:
            """List tool IDs usable when creating a dynamic agent.

            Use with create_agent: pass these IDs in the tools parameter.
            """
            return ListAvailableToolsResult(
                tool_ids=get_available_tool_ids()
            )

        tools_list.append(list_available_tools)

    return tools_list
