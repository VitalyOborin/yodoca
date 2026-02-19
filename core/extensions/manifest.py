"""Extension manifest: Pydantic model and YAML loader.

Capabilities are determined by protocols the class implements, not by a manifest field.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class AgentLimits(BaseModel):
    """Guardrails for agent-extensions."""

    max_turns: int = 10
    max_tokens_per_invocation: int = 50000
    time_budget_ms: int = 120000


class AgentManifestConfig(BaseModel):
    """Agent section in manifest.yaml."""

    integration_mode: Literal["tool", "handoff"] = "tool"
    model: str
    instructions: str = ""
    instructions_file: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    uses_tools: list[str] = Field(default_factory=list)
    limits: AgentLimits = Field(default_factory=AgentLimits)


class ExtensionManifest(BaseModel):
    """Manifest schema for sandbox/extensions/<id>/manifest.yaml."""

    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    entrypoint: str | None = None  # module:ClassName; optional for declarative agents
    natural_language_description: str = ""
    setup_instructions: str = ""
    depends_on: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    agent: AgentManifestConfig | None = None

    @model_validator(mode="after")
    def _validate_entrypoint_or_agent(self) -> "ExtensionManifest":
        if not self.agent and not self.entrypoint:
            raise ValueError("entrypoint is required for non-agent extensions")
        return self


def load_manifest(path: Path) -> ExtensionManifest:
    """Read and validate manifest.yaml. Raises on invalid YAML or validation error."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a YAML object: {path}")
    return ExtensionManifest.model_validate(data)
