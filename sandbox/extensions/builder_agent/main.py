"""Builder Agent extension: programmatic AgentProvider that creates new extensions."""

from agents import Agent, Runner, WebSearchTool

# Builder is the one extension allowed to use core tools (file, patch, shell, restart).
from core.tools import apply_patch_tool, file, request_restart, shell_tool


class BuilderAgentExtension:
    """Programmatic AgentProvider: creates extensions; uses core tools (file, patch, shell, restart)."""

    def __init__(self) -> None:
        self._agent: Agent | None = None
        self._context = None

    async def initialize(self, context) -> None:
        self._context = context
        self._agent = Agent(
            name="ExtensionBuilder",
            instructions=context.resolved_instructions,
            model=context.agent_model,
            tools=[
                WebSearchTool(),
                shell_tool,
                file,
                apply_patch_tool,
                request_restart,
            ],
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return self._agent is not None

    def get_agent_descriptor(self):
        from core.extensions.contract import AgentDescriptor
        return AgentDescriptor(
            name="Extension Builder Agent",
            description=(
                "Use this agent when the user asks to create a new extension, plugin, "
                "tool, channel, or agent. The Builder generates code following the "
                "extension contract and requests a system restart to activate it. "
                "DO NOT use for modifying existing extensions."
            ),
            integration_mode="tool",
        )

    async def invoke(self, task: str, context=None):
        from core.extensions.contract import AgentResponse
        if not self._agent:
            return AgentResponse(status="error", content="", error="Agent not initialized")
        try:
            result = await Runner.run(self._agent, task, max_turns=20)
            return AgentResponse(status="success", content=result.final_output or "")
        except Exception as e:
            return AgentResponse(status="error", content="", error=str(e))
