"""File management tools for the AI agent: unified file operations and atomic patch.

STRICT RESTRICTION: All operations are allowed ONLY within the sandbox directory.
Any path outside sandbox is rejected. No exceptions."""

import shutil
from pathlib import Path
from typing import Literal

from agents import ApplyPatchTool, apply_diff, function_tool
from agents.editor import ApplyPatchOperation, ApplyPatchResult

from core.tools.sandbox import SANDBOX_DIR, resolve_sandbox_path


class SandboxEditor:
    """ApplyPatchEditor implementation scoped to the sandbox directory."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or SANDBOX_DIR).resolve()
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
        return resolve_sandbox_path(
            relative, root=self._root, ensure_parent=ensure_parent
        )


# ApplyPatchTool for atomic patch operations (create, update, delete via diff)
apply_patch_tool = ApplyPatchTool(editor=SandboxEditor())


@function_tool(name_override="file", strict_mode=False)
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
        target = resolve_sandbox_path(path)
        SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

        def _rel(p: Path) -> str:
            return p.relative_to(SANDBOX_DIR).as_posix()

        if action == "read":
            if not target.is_file():
                return f"Error: not a file or does not exist: {_rel(target)}"
            return target.read_text(encoding="utf-8")

        if action == "write":
            if content is None:
                return "Error: content is required for write action"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {_rel(target)}"

        if action == "delete":
            if not target.exists():
                return f"Error: not found: {_rel(target)}"
            if target.is_file():
                target.unlink()
                return f"Deleted file {_rel(target)}"
            if target.is_dir():
                if any(target.iterdir()):
                    return f"Error: directory not empty: {_rel(target)}"
                target.rmdir()
                return f"Deleted directory {_rel(target)}"
            return f"Error: cannot delete: {_rel(target)}"

        if action == "copy":
            if destination is None:
                return "Error: destination is required for copy action"
            dst = resolve_sandbox_path(destination)
            if not target.exists():
                return f"Error: source not found: {_rel(target)}"
            if target.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, dst)
            else:
                shutil.copytree(target, dst)
            return f"Copied {_rel(target)} -> {_rel(dst)}"

        if action == "move":
            if destination is None:
                return "Error: destination is required for move action"
            dst = resolve_sandbox_path(destination)
            if not target.exists():
                return f"Error: source not found: {_rel(target)}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            target.rename(dst)
            return f"Moved {_rel(target)} -> {_rel(dst)}"

        if action == "stat":
            if not target.exists():
                return f"Path does not exist: {_rel(target)}"
            if target.is_file():
                size = target.stat().st_size
                return f"type=file, size={size} bytes, path={_rel(target)}"
            if target.is_dir():
                count = sum(1 for _ in target.iterdir())
                return f"type=directory, entries={count}, path={_rel(target)}"
            return f"type=unknown, path={_rel(target)}"

        if action == "list":
            if not target.is_dir():
                return f"Error: not a directory: {_rel(target)}"
            items = sorted(
                target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            )
            lines = [f"{'[DIR]' if p.is_dir() else '     '} {p.name}" for p in items]
            return "\n".join(lines) if lines else "(empty)"

        return f"Error: unknown action: {action}"
    except RuntimeError as e:
        if "Access denied" in str(e):
            return str(e)
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {e}"
