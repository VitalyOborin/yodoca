"""ExtensionFactory: instantiate extensions from manifests."""

import importlib
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
        module_path = f"sandbox.extensions.{manifest.id}.{module_name}"
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cast(Extension, cls())
