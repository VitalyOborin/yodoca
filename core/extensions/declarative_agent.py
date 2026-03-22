"""DeclarativeAgentAdapter: AgentProvider created from manifest only — no main.py needed."""

from agents import Agent, ModelSettings, Runner

from core.extensions.context import ExtensionContext
from core.extensions.contract import (
    AgentDescriptor,
    AgentInvocationContext,
    AgentResponse,
)
from core.extensions.manifest import ExtensionManifest


class DeclarativeAgentAdapter:
    """AgentProvider created from manifest.yaml — no main.py needed."""

    def __init__(self, manifest: ExtensionManifest) -> None:
        self._manifest = manifest
        self._agent: Agent | None = None

    async def initialize(self, context: ExtensionContext) -> None:
        agent_cfg = self._manifest.agent
        if agent_cfg is None:
            raise RuntimeError("Declarative agent config is missing in manifest")
        if context.model_router and context.agent_id:
            model = context.model_router.get_model(context.agent_id)
        else:
            model = context.agent_model
        self._agent = Agent(
            name=self._manifest.name,
            instructions=context.resolved_instructions,
            model=model,
            tools=context.resolved_tools,
            model_settings=ModelSettings(
                parallel_tool_calls=agent_cfg.parallel_tool_calls
            ),
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return self._agent is not None

    @property
    def agent(self) -> Agent | None:
        """Underlying SDK Agent after initialize(); None before init."""
        return self._agent

    def get_agent_descriptor(self) -> AgentDescriptor:
        agent_cfg = self._manifest.agent
        if agent_cfg is None:
            raise RuntimeError("Declarative agent config is missing in manifest")
        return AgentDescriptor(
            name=self._manifest.name,
            description=self._manifest.description,
            integration_mode=agent_cfg.integration_mode,
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
            agent_cfg = self._manifest.agent
            if agent_cfg is None:
                return AgentResponse(
                    status="error",
                    content="",
                    error="Declarative agent config is missing in manifest",
                )
            result = await Runner.run(
                self._agent,
                task,
                max_turns=agent_cfg.limits.max_turns,
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
