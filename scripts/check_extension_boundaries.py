#!/usr/bin/env python3
"""Verify extensions only import from declared depends_on peers (and self).

Scans sandbox/extensions/*/ for imports of sandbox.extensions.<other_id>.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import yaml
from _boundary_common import green, red

ROOT = Path(__file__).resolve().parent.parent
EXTENSIONS_ROOT = ROOT / "sandbox" / "extensions"
SANDBOX_EXT_PREFIX = "sandbox.extensions."


def _load_manifests() -> dict[str, tuple[Path, list[str]]]:
    """extension_id -> (manifest_path, depends_on list)."""
    out: dict[str, tuple[Path, list[str]]] = {}
    for manifest_path in sorted(EXTENSIONS_ROOT.glob("*/manifest.yaml")):
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        ext_id = data.get("id")
        if not ext_id or not isinstance(ext_id, str):
            continue
        deps = data.get("depends_on") or []
        if not isinstance(deps, list):
            deps = []
        out[ext_id] = (manifest_path, [d for d in deps if isinstance(d, str)])
    return out


def _peer_from_module(module: str | None) -> str | None:
    """Return other extension id if module is sandbox.extensions.<id>.…"""
    if not module or not module.startswith(SANDBOX_EXT_PREFIX):
        return None
    rest = module[len(SANDBOX_EXT_PREFIX) :]
    if not rest:
        return None
    return rest.split(".", 1)[0]


def _collect_imports(tree: ast.AST) -> list[tuple[int, str | None, str]]:
    """(lineno, module for ImportFrom, import path for Import)."""
    found: list[tuple[int, str | None, str]] = []

    class V(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                found.append((node.lineno, None, alias.name))
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level and node.level > 0:
                self.generic_visit(node)
                return
            mod = node.module
            found.append((node.lineno, mod, ""))
            self.generic_visit(node)

    V().visit(tree)
    return found


def _violations_for_file(
    path: Path,
    owner_id: str,
    allowed_peers: set[str],
    verbose: bool,
) -> list[str]:
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        return [f"{path}: syntax error: {e}"]
    msgs: list[str] = []
    for lineno, from_mod, import_name in _collect_imports(tree):
        if from_mod is not None:
            peer = _peer_from_module(from_mod)
            if peer and peer != owner_id and peer not in allowed_peers:
                msgs.append(
                    f"{path}:{lineno}: imports sandbox.extensions.{peer} "
                    f"via '{from_mod}' but '{peer}' is not in depends_on "
                    f"for extension '{owner_id}'"
                )
        elif import_name.startswith(SANDBOX_EXT_PREFIX):
            rest = import_name[len(SANDBOX_EXT_PREFIX) :]
            peer = rest.split(".", 1)[0] if rest else None
            if peer and peer != owner_id and peer not in allowed_peers:
                msgs.append(
                    f"{path}:{lineno}: imports '{import_name}' but extension "
                    f"'{peer}' is not in depends_on for '{owner_id}'"
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

    manifests = _load_manifests()
    if not manifests:
        print(red("No extension manifests found under sandbox/extensions/"))
        return 1

    all_violations: list[str] = []
    for ext_id, (_mpath, depends_on) in sorted(manifests.items()):
        allowed = set(depends_on) | {ext_id}
        ext_dir = EXTENSIONS_ROOT / ext_id
        if not ext_dir.is_dir():
            continue
        for py_path in sorted(ext_dir.rglob("*.py")):
            if "__pycache__" in py_path.parts:
                continue
            all_violations.extend(
                _violations_for_file(py_path, ext_id, allowed, args.verbose)
            )

    if all_violations:
        print(red("Extension boundary violations:"))
        for line in all_violations:
            print(f"  {line}")
        return 1

    print(green("Extension boundary check passed."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
