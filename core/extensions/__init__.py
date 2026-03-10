"""Extension system: contract, manifest, context, router, loader."""

from core.extensions.context import ExtensionContext
from core.extensions.contract import (
    AgentDescriptor,
    AgentInvocationContext,
    AgentProvider,
    AgentResponse,
    ChannelProvider,
    Extension,
    ExtensionState,
    SchedulerProvider,
    ServiceProvider,
    SetupProvider,
    ToolProvider,
    TurnContext,
)
from core.extensions.loader import Loader
from core.extensions.manifest import ExtensionManifest, ScheduleEntry, load_manifest
from core.extensions.routing import MessageRouter

__all__ = [
    "AgentDescriptor",
    "AgentInvocationContext",
    "AgentProvider",
    "AgentResponse",
    "ChannelProvider",
    "Extension",
    "ExtensionState",
    "ExtensionContext",
    "ExtensionManifest",
    "ScheduleEntry",
    "Loader",
    "MessageRouter",
    "ServiceProvider",
    "SchedulerProvider",
    "SetupProvider",
    "ToolProvider",
    "TurnContext",
    "load_manifest",
]
