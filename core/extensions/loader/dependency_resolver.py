"""DependencyResolver: topological ordering for extension manifests."""

from core.extensions.manifest import ExtensionManifest


class DependencyResolver:
    """Resolves extension load order and validates dependency graph."""

    def resolve(self, manifests: list[ExtensionManifest]) -> list[ExtensionManifest]:
        """Return manifests in topological order by depends_on."""
        ids = {manifest.id for manifest in manifests}
        for manifest in manifests:
            for dep in manifest.depends_on:
                if dep not in ids:
                    raise ValueError(
                        f"Extension {manifest.id} depends on missing {dep}"
                    )
        order: list[ExtensionManifest] = []
        seen: set[str] = set()
        visiting: set[str] = set()
        by_id = {manifest.id: manifest for manifest in manifests}
        for manifest in manifests:
            self._visit(manifest, by_id, order, seen, visiting)
        return order

    def _visit(
        self,
        manifest: ExtensionManifest,
        by_id: dict[str, ExtensionManifest],
        order: list[ExtensionManifest],
        seen: set[str],
        visiting: set[str],
    ) -> None:
        if manifest.id in seen:
            return
        if manifest.id in visiting:
            raise ValueError(f"Cycle in depends_on involving {manifest.id}")
        visiting.add(manifest.id)
        for dep in manifest.depends_on:
            dep_manifest = by_id.get(dep)
            if dep_manifest is not None:
                self._visit(dep_manifest, by_id, order, seen, visiting)
        visiting.remove(manifest.id)
        seen.add(manifest.id)
        order.append(manifest)
