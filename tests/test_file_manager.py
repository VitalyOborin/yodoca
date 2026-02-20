"""Tests for file_manager path resolution and sandbox normalization."""

from pathlib import Path

from core.tools.file_manager import _resolve_path


def test_resolve_path_sandbox_prefix_normalized() -> None:
    """sandbox/extensions/foo and extensions/foo resolve to the same location."""
    p1 = _resolve_path("sandbox/extensions/foo")
    p2 = _resolve_path("extensions/foo")
    assert p1 == p2


def test_resolve_path_extensions_foo() -> None:
    """extensions/foo resolves inside sandbox."""
    target = _resolve_path("extensions/foo")
    sandbox = (Path(__file__).resolve().parent.parent / "sandbox").resolve()
    assert str(target).startswith(str(sandbox))
    assert "extensions" in target.parts
    assert "foo" in target.parts
