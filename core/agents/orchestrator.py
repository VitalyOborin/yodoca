"""Agent Orchestrator definition and configuration."""

from pathlib import Path
from typing import Any

from agents import Agent, WebSearchTool
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.settings import get_setting, load_settings
from core.tools import shell_tool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_instructions(spec: str, template_vars: dict[str, Any] | None = None) -> str:
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
        return template.render(**(template_vars or {})).strip()
    return path.read_text(encoding="utf-8").strip()


def create_orchestrator_agent(
    extension_tools: list[Any] | None = None,
    agent_tools: list[Any] | None = None,
    capabilities_summary: str = "",
) -> Agent:
    """Create the Orchestrator agent from config; merge core tools and extension tools."""
    settings = load_settings()
    model = get_setting(settings, "agents.orchestrator.model", "gpt-5.2")
    instructions_spec = get_setting(settings, "agents.orchestrator.instructions", "")
    instructions = _resolve_instructions(
        instructions_spec,
        template_vars={"capabilities": capabilities_summary},
    )
    tools: list[Any] = [WebSearchTool(), shell_tool]
    if extension_tools:
        tools.extend(extension_tools)
    if agent_tools:
        tools.extend(agent_tools)
    return Agent(
        name="Orchestrator",
        instructions=instructions,
        model=model,
        tools=tools,
    )
