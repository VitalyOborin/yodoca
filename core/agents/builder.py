"""Builder Agent: creates extensions by contract. Uses file, apply_patch_tool, request_restart, shell, web search."""

from pathlib import Path

from agents import Agent, WebSearchTool
from core.tools import apply_patch_tool, file, request_restart, shell_tool
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.settings import get_setting, load_settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_instructions(spec: str) -> str:
    """Resolve instructions from config: file path (with optional Jinja2) or literal string."""
    if not spec or not spec.strip():
        return ""
    path = _PROJECT_ROOT / spec.strip()
    if not path.exists() or not path.is_file():
        return spec.strip()
    if path.suffix == ".jinja2" or path.name.endswith(".jinja2"):
        prompts_dir = path.parent
        env = Environment(
            loader=FileSystemLoader(prompts_dir),
            autoescape=select_autoescape(enabled_extensions=()),
        )
        template = env.get_template(path.name)
        return template.render().strip()
    return path.read_text(encoding="utf-8").strip()


def create_builder_agent() -> Agent:
    """Create and return the Builder Agent from config (model, instructions).

    Operates within sandbox with file, patch, restart, shell, and web search tools.
    """
    settings = load_settings()
    model = get_setting(settings, "agents.builder.model", "gpt-5.2-codex")
    instructions_spec = get_setting(settings, "agents.builder.instructions", "")
    instructions = _resolve_instructions(instructions_spec)
    return Agent(
        name="ExtensionBuilder",
        instructions=instructions,
        model=model,
        tools=[
            WebSearchTool(),
            shell_tool,
            file,
            apply_patch_tool,
            request_restart,
        ],
    )
