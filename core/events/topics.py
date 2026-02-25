"""System-guaranteed EventBus topics. Always have a registered handler in the kernel."""


class SystemTopics:
    """Guaranteed system topics. Handlers are registered by the kernel."""

    # Deliver a message to the user immediately via all active channels
    USER_NOTIFY = "system.user.notify"

    # Invoke the Orchestrator agent with a prompt; response goes to user
    AGENT_TASK = "system.agent.task"

    # Invoke the Orchestrator silently; no response to user
    AGENT_BACKGROUND = "system.agent.background"

    # Session rotated due to inactivity; triggers consolidation
    SESSION_COMPLETED = "session.completed"

    # Request secure input from a channel (secret collection without LLM exposure)
    SECURE_INPUT_REQUEST = "system.channel.secure_input_request"


# Payload contracts (documentation + runtime validation)
USER_NOTIFY_PAYLOAD = {"text": "str", "channel_id": "str | None"}
AGENT_TASK_PAYLOAD = {"prompt": "str", "channel_id": "str | None", "correlation_id": "str | None"}
AGENT_BACKGROUND_PAYLOAD = {"prompt": "str", "correlation_id": "str | None"}
SESSION_COMPLETED_PAYLOAD = {"session_id": "str", "reason": "str"}
SECURE_INPUT_REQUEST_PAYLOAD = {"secret_id": "str", "prompt": "str", "target_channel": "str"}
