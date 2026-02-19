"""DeclarativeAgentAdapter: AgentProvider created from manifest only — no main.py needed."""

from pathlib import Path
from typing import Any

from agents import Agent, Runner
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.extensions.contract import (
    AgentDescriptor,
    AgentInvocationContext,
    AgentProvider,
    AgentResponse,
    Extension,
)
from core.extensions.context import ExtensionContext
from core.extensions.manifest import ExtensionManifest


def _resolve_instructions(
    spec: str,
    extension_dir: Path,
    project_root: Path,
    template_vars: dict[str, Any] | None = None,
) -> str:
    """Resolve instructions: try extension dir, then project root. Support Jinja2 or plain file."""
    if not spec or not spec.strip():
        return ""
    spec_stripped = spec.strip()
    for base in (extension_dir, project_root):
        path = base / spec_stripped
        if path.exists() and path.is_file():
            if path.suffix == ".jinja2" or path.name.endswith(".jinja2"):
                env = Environment(
                    loader=FileSystemLoader(path.parent),
                    autoescape=select_autoescape(enabled_extensions=()),
                )
                template = env.get_template(path.name)
                return template.render(**(template_vars or {})).strip()
            return path.read_text(encoding="utf-8").strip()
    return spec_stripped


class DeclarativeAgentAdapter:
    """AgentProvider created from manifest.yaml — no main.py needed."""

    def __init__(self, manifest: ExtensionManifest) -> None:
        self._manifest = manifest
        self._agent: Agent | None = None

    async def initialize(self, context: ExtensionContext) -> None:
        extension_dir = context._data_dir_path.parent.parent / "extensions" / self._manifest.id
        project_root = context._data_dir_path.parent.parent.parent
        instructions = _resolve_instructions(
            self._manifest.agent.instructions,
            extension_dir,
            project_root,
        )
        self._agent = Agent(
            name=self._manifest.name,
            instructions=instructions,
            model=self._manifest.agent.model,
            tools=context.resolved_tools,
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return self._agent is not None

    def get_agent_descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            name=self._manifest.name,
            description=self._manifest.natural_language_description,
            integration_mode=self._manifest.agent.integration_mode,
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
            result = await Runner.run(
                self._agent,
                task,
                max_turns=self._manifest.agent.limits.max_turns,
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
