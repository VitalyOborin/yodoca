"""Tool to request Supervisor to restart the AI agent. Use after code changes (extensions, prompts)."""

from pathlib import Path
from typing import Any

from agents import function_tool


def make_restart_tool(restart_file_path: Path) -> Any:
    """Create a request_restart tool that writes to the given path.

    Args:
        restart_file_path: Path to the restart flag file (e.g. sandbox/.restart_requested).
    """

    @function_tool(name_override="request_restart", needs_approval=True)
    def request_restart(reason: str) -> str:
        """Request the Supervisor to restart the AI agent.

        Use after code changes (extensions, prompts) so the agent picks up new code.
        Requires user approval before execution.

        Args:
            reason: Why the restart is needed (e.g. 'Extension code updated').
        """
        restart_file_path.parent.mkdir(parents=True, exist_ok=True)
        restart_file_path.write_text(reason or "restart requested", encoding="utf-8")
        return "Restart requested. Supervisor will restart the agent shortly."

    return request_restart
