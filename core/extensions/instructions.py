"""Kernel helper: resolve agent instructions from inline text and/or file path.

Used by Loader for all agent-extensions (declarative and programmatic).
"""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _load_instructions_file(
    path: Path, template_vars: dict[str, Any] | None
) -> str:
    """Load and optionally render template. Returns stripped content or empty string."""
    if path.suffix == ".jinja2" or path.name.endswith(".jinja2"):
        env = Environment(
            loader=FileSystemLoader(path.parent),
            autoescape=select_autoescape(enabled_extensions=()),
        )
        return env.get_template(path.name).render(**(template_vars or {})).strip()
    return path.read_text(encoding="utf-8").strip()


def resolve_instructions(
    instructions: str = "",
    instructions_file: str = "",
    extension_dir: Path | None = None,
    template_vars: dict[str, Any] | None = None,
) -> str:
    """Resolve combined instructions from optional inline text and/or file path."""
    parts: list[str] = []
    if instructions_file and instructions_file.strip() and extension_dir:
        spec = instructions_file.strip()
        path = extension_dir / spec
        if path.exists() and path.is_file():
            content = _load_instructions_file(path, template_vars)
            if content:
                parts.append(content)
    if instructions and instructions.strip():
        parts.append(instructions.strip())
    return "\n\n".join(parts).strip() if parts else ""
