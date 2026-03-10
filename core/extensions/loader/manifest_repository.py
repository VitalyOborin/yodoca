"""ManifestRepository: discover and query extension manifests."""

from pathlib import Path

from core.extensions.manifest import ExtensionManifest, load_manifest


class ManifestRepository:
    """Manifest access layer for extension discovery and lookup."""

    def __init__(self, extensions_dir: Path) -> None:
        self._extensions_dir = extensions_dir
        self._manifests: list[ExtensionManifest] = []

    @property
    def manifests(self) -> list[ExtensionManifest]:
        """Current discovered manifests."""
        return self._manifests

    async def discover(self) -> list[ExtensionManifest]:
        """Scan extensions_dir for manifest.yaml and keep only enabled manifests."""
        manifests: list[ExtensionManifest] = []
        if not self._extensions_dir.exists():
            self._manifests = manifests
            return manifests
        for extension_dir in sorted(self._extensions_dir.iterdir()):
            if not extension_dir.is_dir():
                continue
            manifest_path = extension_dir / "manifest.yaml"
            if not manifest_path.exists():
                continue
            manifest = load_manifest(manifest_path)
            if manifest.enabled:
                manifests.append(manifest)
        self._manifests = manifests
        return manifests

    def set_manifests(self, manifests: list[ExtensionManifest]) -> None:
        """Override manifests (used by tests and compatibility paths)."""
        self._manifests = manifests

    def get_manifest(self, ext_id: str) -> ExtensionManifest | None:
        """Return manifest by extension id."""
        return next(
            (manifest for manifest in self._manifests if manifest.id == ext_id),
            None,
        )
