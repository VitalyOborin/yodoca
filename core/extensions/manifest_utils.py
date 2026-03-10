"""Helpers shared by loader and event wiring."""

from collections.abc import Iterator

from core.extensions.contract import ExtensionState
from core.extensions.manifest import ExtensionManifest


def iter_active_manifests(
    manifests: list[ExtensionManifest],
    state: dict[str, ExtensionState],
) -> Iterator[tuple[str, ExtensionManifest]]:
    """Yield manifests that are not currently in ERROR state."""
    for manifest in manifests:
        ext_id = manifest.id
        if state.get(ext_id) == ExtensionState.ERROR:
            continue
        yield ext_id, manifest
