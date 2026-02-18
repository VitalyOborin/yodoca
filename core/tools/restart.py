"""Tool to request Supervisor to restart the AI agent. Use after code changes (extensions, prompts)."""

from pathlib import Path

from agents import function_tool

from core.settings import load_settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_restart_file_path() -> Path:
    """Restart file path from config (relative to project root)."""
    settings = load_settings()
    rel = settings.get("supervisor_restart_file", "sandbox/.restart_requested")
    return _PROJECT_ROOT / rel


@function_tool(name_override="request_restart", needs_approval=True)
def request_restart(reason: str) -> str:
    """Request the Supervisor to restart the AI agent.

    Use after code changes (extensions, prompts) so the agent picks up new code.
    Requires user approval before execution.

    Args:
        reason: Why the restart is needed (e.g. 'Extension code updated').
    """
    path = _get_restart_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reason or "restart requested", encoding="utf-8")
    return "Restart requested. Supervisor will restart the agent shortly."
