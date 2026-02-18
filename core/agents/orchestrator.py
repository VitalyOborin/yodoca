"""Agent Orchestrator definition and configuration."""

import asyncio
from pathlib import Path

from agents import Agent, Runner
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.settings import get_setting, load_settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Hardcoded request for the minimal demo.
HARDCODED_REQUEST = "Say hello in one short sentence."


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


def create_orchestrator_agent() -> Agent:
    """Create the Orchestrator agent from config (model, instructions)."""
    settings = load_settings()
    model = get_setting(settings, "agents.orchestrator.model", "gpt-5.2")
    instructions_spec = get_setting(settings, "agents.orchestrator.instructions", "")
    instructions = _resolve_instructions(instructions_spec)
    return Agent(
        name="Orchestrator",
        instructions=instructions,
        model=model,
    )


async def run_once() -> str:
    """Run the orchestrator once with a hardcoded request; return final output."""
    agent = create_orchestrator_agent()
    result = await Runner.run(agent, HARDCODED_REQUEST)
    return result.final_output or ""


async def main_async() -> None:
    """Entry point for the AI agent process: run once and print to CLI."""
    output = await run_once()
    print(output)


def main() -> None:
    """Synchronous entry for the AI agent process."""
    asyncio.run(main_async())
