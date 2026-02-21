"""Extension system: contract, manifest, context, router, loader."""

from core.extensions.contract import (
    AgentDescriptor,
    AgentInvocationContext,
    AgentProvider,
    AgentResponse,
    ChannelProvider,
    Extension,
    ServiceProvider,
    SchedulerProvider,
    SetupProvider,
    ToolProvider,
)
from core.extensions.loader import Loader
from core.extensions.manifest import ExtensionManifest, ScheduleEntry, load_manifest
from core.extensions.router import MessageRouter
from core.extensions.context import ExtensionContext

__all__ = [
    "AgentDescriptor",
    "AgentInvocationContext",
    "AgentProvider",
    "AgentResponse",
    "ChannelProvider",
    "Extension",
    "ExtensionContext",
    "ExtensionManifest",
    "ScheduleEntry",
    "Loader",
    "MessageRouter",
    "ServiceProvider",
    "SchedulerProvider",
    "SetupProvider",
    "ToolProvider",
    "load_manifest",
]
