"""File management tools for the AI agent: unified file operations and atomic patch.

STRICT RESTRICTION: All operations are allowed ONLY within the sandbox directory.
Any path outside sandbox is rejected. No exceptions."""

import shutil
from pathlib import Path
from typing import Literal

from agents import ApplyPatchTool, apply_diff, function_tool
from agents.editor import ApplyPatchOperation, ApplyPatchResult
from pydantic import BaseModel, Field

from core.tools.sandbox import SANDBOX_DIR, resolve_sandbox_path


class FileResult(BaseModel):
    """Result of file tool. Unified structure for all actions."""

    success: bool
    action: str = ""
    path: str = ""
    content: str | None = None
    message: str = ""
    entries: list[str] = Field(default_factory=list)
    error: str | None = None


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


def _rel(p: Path) -> str:
    return p.relative_to(SANDBOX_DIR).as_posix()


def _file_read(target: Path) -> FileResult:
    if not target.is_file():
        return FileResult(
            success=False,
            action="read",
            path=_rel(target),
            error=f"not a file or does not exist: {_rel(target)}",
        )
    return FileResult(
        success=True,
        action="read",
        path=_rel(target),
        content=target.read_text(encoding="utf-8"),
    )


def _file_write(target: Path, content: str | None) -> FileResult:
    if content is None:
        return FileResult(
            success=False,
            action="write",
            error="content is required for write action",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    rel_path = _rel(target)
    return FileResult(
        success=True, action="write", path=rel_path, message=f"Wrote {rel_path}"
    )


def _file_delete(target: Path) -> FileResult:
    if not target.exists():
        rel_path = _rel(target)
        return FileResult(
            success=False,
            action="delete",
            path=rel_path,
            error=f"not found: {rel_path}",
        )
    if target.is_file():
        target.unlink()
        rel_path = _rel(target)
        return FileResult(
            success=True,
            action="delete",
            path=rel_path,
            message=f"Deleted file {rel_path}",
        )
    if target.is_dir():
        if any(target.iterdir()):
            return FileResult(
                success=False,
                action="delete",
                path=_rel(target),
                error=f"directory not empty: {_rel(target)}",
            )
        target.rmdir()
        rel_path = _rel(target)
        return FileResult(
            success=True,
            action="delete",
            path=rel_path,
            message=f"Deleted directory {rel_path}",
        )
    return FileResult(
        success=False,
        action="delete",
        path=_rel(target),
        error=f"cannot delete: {_rel(target)}",
    )


def _file_copy(target: Path, destination: str | None) -> FileResult:
    if destination is None:
        return FileResult(
            success=False,
            action="copy",
            error="destination is required for copy action",
        )
    dst = resolve_sandbox_path(destination)
    if not target.exists():
        return FileResult(
            success=False,
            action="copy",
            path=_rel(target),
            error=f"source not found: {_rel(target)}",
        )
    if target.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, dst)
    else:
        shutil.copytree(target, dst)
    return FileResult(
        success=True,
        action="copy",
        path=_rel(target),
        message=f"Copied {_rel(target)} -> {_rel(dst)}",
    )


def _file_move(target: Path, destination: str | None) -> FileResult:
    if destination is None:
        return FileResult(
            success=False,
            action="move",
            error="destination is required for move action",
        )
    dst = resolve_sandbox_path(destination)
    if not target.exists():
        return FileResult(
            success=False,
            action="move",
            path=_rel(target),
            error=f"source not found: {_rel(target)}",
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    target.rename(dst)
    return FileResult(
        success=True,
        action="move",
        path=_rel(target),
        message=f"Moved {_rel(target)} -> {_rel(dst)}",
    )


def _file_stat(target: Path) -> FileResult:
    if not target.exists():
        return FileResult(
            success=True,
            action="stat",
            path=_rel(target),
            message=f"Path does not exist: {_rel(target)}",
        )
    if target.is_file():
        msg = f"type=file, size={target.stat().st_size} bytes, path={_rel(target)}"
        return FileResult(success=True, action="stat", path=_rel(target), message=msg)
    if target.is_dir():
        n = sum(1 for _ in target.iterdir())
        msg = f"type=directory, entries={n}, path={_rel(target)}"
        return FileResult(success=True, action="stat", path=_rel(target), message=msg)
    return FileResult(
        success=True,
        action="stat",
        path=_rel(target),
        message=f"type=unknown, path={_rel(target)}",
    )


def _file_list(target: Path) -> FileResult:
    if not target.is_dir():
        return FileResult(
            success=False,
            action="list",
            path=_rel(target),
            error=f"not a directory: {_rel(target)}",
        )
    items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    entries = [f"{'[DIR]' if p.is_dir() else '     '} {p.name}" for p in items]
    return FileResult(success=True, action="list", path=_rel(target), entries=entries)


@function_tool(name_override="file", strict_mode=False)
def file(
    action: Literal["read", "write", "delete", "copy", "move", "stat", "list"],
    path: str,
    content: str | None = None,
    destination: str | None = None,
) -> FileResult:
    """Unified file operations. Works ONLY within sandbox; paths outside are rejected."""
    try:
        target = resolve_sandbox_path(path)
        SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
        if action == "read":
            return _file_read(target)
        if action == "write":
            return _file_write(target, content)
        if action == "delete":
            return _file_delete(target)
        if action == "copy":
            return _file_copy(target, destination)
        if action == "move":
            return _file_move(target, destination)
        if action == "stat":
            return _file_stat(target)
        if action == "list":
            return _file_list(target)
        return FileResult(
            success=False, action=action, error=f"unknown action: {action}"
        )
    except RuntimeError as e:
        err = str(e) if "Access denied" in str(e) else f"Error: {e}"
        return FileResult(success=False, action=action, path=path, error=err)
    except Exception as e:
        return FileResult(success=False, action=action, path=path, error=str(e))
