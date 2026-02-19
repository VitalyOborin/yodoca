"""DeclarativeAgentAdapter: AgentProvider created from manifest only — no main.py needed."""

from agents import Agent, Runner

from core.extensions.contract import (
    AgentDescriptor,
    AgentInvocationContext,
    AgentProvider,
    AgentResponse,
)
from core.extensions.context import ExtensionContext
from core.extensions.manifest import ExtensionManifest


class DeclarativeAgentAdapter:
    """AgentProvider created from manifest.yaml — no main.py needed."""

    def __init__(self, manifest: ExtensionManifest) -> None:
        self._manifest = manifest
        self._agent: Agent | None = None

    async def initialize(self, context: ExtensionContext) -> None:
        if context.model_router and context.agent_id:
            model = context.model_router.get_model(context.agent_id)
        else:
            model = context.agent_model
        self._agent = Agent(
            name=self._manifest.name,
            instructions=context.resolved_instructions,
            model=model,
            tools=context.resolved_tools,
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return self._agent is not None

    def get_agent_descriptor(self) -> AgentDescriptor:
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
