"""Extension system: contract, manifest, context, router, loader."""

from core.extensions.contract import (
    ChannelProvider,
    Extension,
    ServiceProvider,
    SchedulerProvider,
    SetupProvider,
    ToolProvider,
)
from core.extensions.loader import Loader
from core.extensions.manifest import ExtensionManifest, load_manifest
from core.extensions.router import MessageRouter
from core.extensions.context import ExtensionContext

__all__ = [
    "ChannelProvider",
    "Extension",
    "ExtensionContext",
    "ExtensionManifest",
    "Loader",
    "MessageRouter",
    "ServiceProvider",
    "SchedulerProvider",
    "SetupProvider",
    "ToolProvider",
    "load_manifest",
]
