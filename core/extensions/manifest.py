"""Extension manifest: Pydantic model and YAML loader.

Capabilities are determined by protocols the class implements, not by a manifest field.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ExtensionManifest(BaseModel):
    """Manifest schema for sandbox/extensions/<id>/manifest.yaml."""

    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    entrypoint: str  # module:ClassName
    natural_language_description: str = ""
    setup_instructions: str = ""
    depends_on: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    enabled: bool = True


def load_manifest(path: Path) -> ExtensionManifest:
    """Read and validate manifest.yaml. Raises on invalid YAML or validation error."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a YAML object: {path}")
    return ExtensionManifest.model_validate(data)
