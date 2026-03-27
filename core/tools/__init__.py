"""Custom tools for the AI agent."""

from core.tools.configure_extension import make_configure_extension_tool
from core.tools.extensions_doctor import make_extensions_doctor_tool
from core.tools.file_manager import apply_patch_tool, file
from core.tools.restart import make_restart_tool
from core.tools.secure_input import make_secure_input_tool

__all__ = [
    "apply_patch_tool",
    "file",
    "make_configure_extension_tool",
    "make_extensions_doctor_tool",
    "make_restart_tool",
    "make_secure_input_tool",
]
