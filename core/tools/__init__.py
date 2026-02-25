"""Custom tools for the AI agent."""

from core.tools.file_manager import apply_patch_tool, file
from core.tools.restart import request_restart
from core.tools.secure_input import make_secure_input_tool
from core.tools.shell_exec import shell_tool

__all__ = [
    "apply_patch_tool",
    "file",
    "make_secure_input_tool",
    "request_restart",
    "shell_tool",
]
