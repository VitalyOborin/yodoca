"""System-guaranteed EventBus topics. Always have a registered handler in the kernel."""


class SystemTopics:
    """Guaranteed system topics. Handlers are registered by the kernel."""

    # Deliver a message to the user immediately via all active channels
    USER_NOTIFY = "system.user.notify"

    # Invoke the Orchestrator agent with a prompt; response goes to user
    AGENT_TASK = "system.agent.task"

    # Invoke the Orchestrator silently; no response to user
    AGENT_BACKGROUND = "system.agent.background"

    # Thread rotated due to inactivity; triggers consolidation
    THREAD_COMPLETED = "thread.completed"

    # Request secure input from a channel (secret collection without LLM exposure)
    SECURE_INPUT_REQUEST = "system.channel.secure_input_request"

    # MCP tool approval: pause run, ask user, resume on approve/reject
    MCP_TOOL_APPROVAL_REQUEST = "system.mcp.tool_approval_request"
    MCP_TOOL_APPROVAL_RESPONSE = "system.mcp.tool_approval_response"

    # Extension lifecycle/admission failure for diagnostics and observability
    EXTENSION_ERROR = "system.extension.error"
