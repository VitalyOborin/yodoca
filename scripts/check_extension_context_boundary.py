#!/usr/bin/env python3
"""Verify extensions only import from the public core API allowlist.

Scans sandbox/extensions/**/*.py for imports from core.* and flags internal modules.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

from _boundary_common import green, red

ROOT = Path(__file__).resolve().parent.parent
EXTENSIONS_ROOT = ROOT / "sandbox" / "extensions"

# fmt: allowlist — public core API for extensions (longest match wins via sort order)
_ALLOWED_EXACT = frozenset({"core.extensions"})

_ALLOWED_PREFIXES: tuple[str, ...] = tuple(
    sorted(
        (
            "core.extensions.context",
            "core.extensions.contract",
            "core.extensions.manifest",
            "core.extensions.persistence.models",
            "core.extensions.update_fields",
            "core.extensions.instructions",
            "core.extensions.declarative_agent",
            "core.events.topics",
            "core.events.models",
            "core.llm.capabilities",
            "core.utils.formatting",
        ),
        key=len,
        reverse=True,
    )
)


def _is_allowed_core_module(module: str) -> bool:
    if module in _ALLOWED_EXACT:
        return True
    for prefix in _ALLOWED_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


def _collect_core_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """(lineno, full module path imported from)."""
    found: list[tuple[int, str]] = []

    class V(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                name = alias.name
                if name == "core" or name.startswith("core."):
                    found.append((node.lineno, name))
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level and node.level > 0:
                self.generic_visit(node)
                return
            mod = node.module
            if mod is None:
                self.generic_visit(node)
                return
            if mod == "core" or mod.startswith("core."):
                found.append((node.lineno, mod))
            self.generic_visit(node)

    V().visit(tree)
    return found


def _check_file(path: Path, verbose: bool) -> list[str]:
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        return [f"{path}: syntax error: {e}"]
    msgs: list[str] = []
    for lineno, mod in _collect_core_imports(tree):
        if not _is_allowed_core_module(mod):
            msgs.append(
                f"{path}:{lineno}: forbidden core import '{mod}' "
                f"(not in public extension API allowlist)"
            )
    if verbose and not msgs:
        print(f"  OK {path.relative_to(ROOT)}")
    return msgs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print per-file OK lines"
    )
    args = parser.parse_args()

    if not EXTENSIONS_ROOT.is_dir():
        print(red("sandbox/extensions/ not found"))
        return 1

    all_msgs: list[str] = []
    for py_path in sorted(EXTENSIONS_ROOT.rglob("*.py")):
        if "__pycache__" in py_path.parts:
            continue
        all_msgs.extend(_check_file(py_path, args.verbose))

    if all_msgs:
        print(red("Extension core-import boundary violations:"))
        for line in all_msgs:
            print(f"  {line}")
        return 1

    print(green("Extension core-import boundary check passed."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
