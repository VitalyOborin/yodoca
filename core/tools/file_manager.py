"""File management tools for the AI agent: unified file operations and atomic patch.

STRICT RESTRICTION: All operations are allowed ONLY within the sandbox directory.
Any path outside sandbox is rejected. No exceptions."""
import shutil
from pathlib import Path
from typing import Literal

from agents import ApplyPatchTool, apply_diff, function_tool
from agents.editor import ApplyPatchOperation, ApplyPatchResult

# Sandbox root: canonical path, agent work directory per README
_SANDBOX_DIR = (Path(__file__).resolve().parent.parent.parent / "sandbox").resolve()

_ACCESS_DENIED_MSG = "Access denied: operations outside sandbox are prohibited."


def _resolve_path(path: str) -> Path:
    """Resolve path relative to sandbox. Rejects any path outside sandbox."""
    candidate = Path(path)
    target = candidate if candidate.is_absolute() else (_SANDBOX_DIR / candidate)
    target = target.resolve()
    try:
        target.relative_to(_SANDBOX_DIR)
    except ValueError:
        raise RuntimeError(f"{_ACCESS_DENIED_MSG} Path: {path}") from None
    return target


class SandboxEditor:
    """ApplyPatchEditor implementation scoped to the sandbox directory."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or _SANDBOX_DIR).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def create_file(self, operation: ApplyPatchOperation) -> ApplyPatchResult:
        relative = self._relative_path(operation.path)
        target = self._resolve(operation.path, ensure_parent=True)
        diff = operation.diff or ""
        content = apply_diff("", diff, mode="create")
        target.write_text(content, encoding="utf-8")
        return ApplyPatchResult(output=f"Created {relative}")

    def update_file(self, operation: ApplyPatchOperation) -> ApplyPatchResult:
        relative = self._relative_path(operation.path)
        target = self._resolve(operation.path)
        original = target.read_text(encoding="utf-8")
        diff = operation.diff or ""
        patched = apply_diff(original, diff)
        target.write_text(patched, encoding="utf-8")
        return ApplyPatchResult(output=f"Updated {relative}")

    def delete_file(self, operation: ApplyPatchOperation) -> ApplyPatchResult:
        relative = self._relative_path(operation.path)
        target = self._resolve(operation.path)
        target.unlink(missing_ok=True)
        return ApplyPatchResult(output=f"Deleted {relative}")

    def _relative_path(self, value: str) -> str:
        resolved = self._resolve(value)
        return resolved.relative_to(self._root).as_posix()

    def _resolve(self, relative: str, ensure_parent: bool = False) -> Path:
        candidate = Path(relative)
        target = candidate if candidate.is_absolute() else (self._root / candidate)
        target = target.resolve()
        try:
            target.relative_to(self._root)
        except ValueError:
            raise RuntimeError(f"{_ACCESS_DENIED_MSG} Path: {relative}") from None
        if ensure_parent:
            target.parent.mkdir(parents=True, exist_ok=True)
        return target


# ApplyPatchTool for atomic patch operations (create, update, delete via diff)
apply_patch_tool = ApplyPatchTool(editor=SandboxEditor())


@function_tool(name_override="file")
def file(
    action: Literal["read", "write", "delete", "copy", "move", "stat", "list"],
    path: str,
    content: str | None = None,
    destination: str | None = None,
) -> str:
    """Unified file operations. Works ONLY within the sandbox directory; paths outside are rejected.

    Args:
        action: Operation to perform.
            read: read file contents
            write: create new file or overwrite existing with content
            delete: delete file or empty directory
            copy: copy file or directory to destination
            move: rename or move file/directory to destination
            stat: check existence and get info (type, size)
            list: list files and directories in path
        path: Path relative to sandbox. Absolute paths must be inside sandbox; otherwise rejected.
        content: Required for write. Content to write.
        destination: Required for copy and move. Target path.
    """
    try:
        target = _resolve_path(path)
        _SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

        if action == "read":
            if not target.is_file():
                return f"Error: not a file or does not exist: {path}"
            return target.read_text(encoding="utf-8")

        if action == "write":
            if content is None:
                return "Error: content is required for write action"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {path}"

        if action == "delete":
            if not target.exists():
                return f"Error: not found: {path}"
            if target.is_file():
                target.unlink()
                return f"Deleted file {path}"
            if target.is_dir():
                if any(target.iterdir()):
                    return f"Error: directory not empty: {path}"
                target.rmdir()
                return f"Deleted directory {path}"
            return f"Error: cannot delete: {path}"

        if action == "copy":
            if destination is None:
                return "Error: destination is required for copy action"
            dst = _resolve_path(destination)
            if not target.exists():
                return f"Error: source not found: {path}"
            if target.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, dst)
            else:
                shutil.copytree(target, dst)
            return f"Copied {path} -> {destination}"

        if action == "move":
            if destination is None:
                return "Error: destination is required for move action"
            dst = _resolve_path(destination)
            if not target.exists():
                return f"Error: source not found: {path}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            target.rename(dst)
            return f"Moved {path} -> {destination}"

        if action == "stat":
            if not target.exists():
                return f"Path does not exist: {path}"
            if target.is_file():
                size = target.stat().st_size
                return f"type=file, size={size} bytes, path={path}"
            if target.is_dir():
                count = sum(1 for _ in target.iterdir())
                return f"type=directory, entries={count}, path={path}"
            return f"type=unknown, path={path}"

        if action == "list":
            if not target.is_dir():
                return f"Error: not a directory: {path}"
            items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            lines = [f"{'[DIR]' if p.is_dir() else '     '} {p.name}" for p in items]
            return "\n".join(lines) if lines else "(empty)"

        return f"Error: unknown action: {action}"
    except RuntimeError as e:
        if "Access denied" in str(e):
            return str(e)
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {e}"
