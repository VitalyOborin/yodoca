"""ExtensionFactory: instantiate extensions from manifests."""

import importlib.util
import sys
from pathlib import Path
from typing import cast

from core.extensions.contract import Extension
from core.extensions.manifest import ExtensionManifest


class ExtensionFactory:
    """Creates extension instances from extension manifests."""

    def __init__(self, extensions_dir: Path) -> None:
        self._extensions_dir = extensions_dir

    def create(self, manifest: ExtensionManifest) -> Extension:
        """Instantiate one extension (declarative agent or programmatic extension)."""
        if manifest.agent and not manifest.entrypoint:
            from core.extensions.declarative_agent import DeclarativeAgentAdapter

            return DeclarativeAgentAdapter(manifest)
        if manifest.entrypoint is None:
            raise ValueError(
                "Extension "
                f"{manifest.id} must have entrypoint for programmatic extensions"
            )
        module_name, class_name = manifest.entrypoint.split(":", 1)
        extension_dir = self._extensions_dir / manifest.id
        py_path = extension_dir / f"{module_name}.py"
        if not py_path.exists():
            raise FileNotFoundError(f"{py_path} not found")
        spec = importlib.util.spec_from_file_location(
            f"ext_{manifest.id}_{module_name}", py_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {py_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls = getattr(module, class_name)
        return cast(Extension, cls())
