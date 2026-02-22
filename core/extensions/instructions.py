"""Kernel helper: resolve agent instructions from inline text and/or file path.

Used by Loader for all agent-extensions (declarative and programmatic).
Single source of truth — avoids duplicating resolution logic in each agent.
"""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


def resolve_instructions(
    instructions: str = "",
    instructions_file: str = "",
    extension_dir: Path | None = None,
    template_vars: dict[str, Any] | None = None,
) -> str:
    """Resolve combined instructions from optional inline text and/or file path.

    - instructions: inline text (optional).
    - instructions_file: path to file, relative to extension_dir (optional).
      Supports .jinja2 templates; plain text otherwise.
      Only extension dir is searched; project prompts/ is system-only.
    - If both are set, file content comes first, then a newline, then inline text.

    Returns combined string, stripped. Empty if neither source is provided.
    """
    parts: list[str] = []
    if instructions_file and instructions_file.strip() and extension_dir:
        spec = instructions_file.strip()
        path = extension_dir / spec
        if path.exists() and path.is_file():
            if path.suffix == ".jinja2" or path.name.endswith(".jinja2"):
                env = Environment(
                    loader=FileSystemLoader(path.parent),
                    autoescape=select_autoescape(enabled_extensions=()),
                )
                template = env.get_template(path.name)
                content = template.render(**(template_vars or {})).strip()
            else:
                content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
    if instructions and instructions.strip():
        parts.append(instructions.strip())
    return "\n\n".join(parts).strip() if parts else ""
