"""Agent Orchestrator definition and configuration."""

from pathlib import Path
from typing import Any

from agents import Agent, ModelSettings
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.llm import ModelRouter
from core.settings_models import AppSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_instructions(
    spec: str, template_vars: dict[str, Any] | None = None
) -> str:
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
    model_router: ModelRouter,
    settings: AppSettings,
    extension_tools: list[Any] | None = None,
    delegation_tools: list[Any] | None = None,
    capabilities_summary: str = "",
    channel_tools: list[Any] | None = None,
) -> Agent:
    """Create the Orchestrator agent from config; merge core tools and extension tools."""
    orch = settings.agents.get("orchestrator")
    _default_orch_instructions = "sandbox/prompts/orchestrator.jinja2"
    if orch and orch.instructions.strip():
        instructions_spec = orch.instructions
    else:
        instructions_spec = _default_orch_instructions
    instructions = _resolve_instructions(
        instructions_spec,
        template_vars={"capabilities": capabilities_summary},
    )
    model = model_router.get_model("orchestrator")
    tools: list[Any] = []
    if extension_tools:
        tools.extend(extension_tools)
    if delegation_tools:
        tools.extend(delegation_tools)
    if channel_tools:
        tools.extend(channel_tools)
    return Agent(
        name="Orchestrator",
        instructions=instructions,
        model=model,
        model_settings=ModelSettings(parallel_tool_calls=True),
        tools=tools,
    )
