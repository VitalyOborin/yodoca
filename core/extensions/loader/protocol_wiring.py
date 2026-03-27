"""Protocol detection: wire ToolProvider, ChannelProvider, AgentProvider, SchedulerProvider."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from core.extensions.contract import (
    AgentProvider,
    ChannelProvider,
    Extension,
    ExtensionState,
    SchedulerProvider,
    SetupProvider,
    ToolProvider,
)
from core.extensions.manifest import ExtensionManifest
from core.extensions.routing.router import MessageRouter
from core.extensions.routing.scheduler_manager import SchedulerManager
from core.settings_models import AppSettings

if TYPE_CHECKING:
    from core.agents.registry import AgentRegistry

logger = logging.getLogger(__name__)


class ProtocolWiringManager:
    """Detects extension protocols and registers channels, agents, schedulers, tool list."""

    def __init__(
        self,
        extensions: dict[str, Extension],
        state: dict[str, ExtensionState],
        settings: AppSettings,
        agent_registry: "AgentRegistry | None",
        get_manifest: Callable[[str], ExtensionManifest | None],
    ) -> None:
        self._extensions = extensions
        self._state = state
        self._settings = settings
        self._agent_registry = agent_registry
        self._get_manifest = get_manifest

    def _register_static_agent(
        self,
        ext_id: str,
        ext: AgentProvider,
        manifest: ExtensionManifest,
    ) -> None:
        """Register AgentProvider with agent_registry. No-op if registry unavailable."""
        if not self._agent_registry:
            return
        from core.agents.registry import AgentRecord

        descriptor = ext.get_agent_descriptor()
        record = AgentRecord(
            id=ext_id,
            name=descriptor.name,
            description=descriptor.description,
            model=manifest.agent.model if manifest.agent else None,
            integration_mode=descriptor.integration_mode,
            tools=manifest.agent.uses_tools if manifest.agent else [],
            limits=manifest.agent.limits if manifest.agent else None,
            source="static",
        )
        self._agent_registry.register(record, ext)

    def detect_and_wire(
        self, router: MessageRouter, manifests: list[ExtensionManifest]
    ) -> tuple[list[ToolProvider], SchedulerManager]:
        """Detect protocols via isinstance; wire ToolProvider, ChannelProvider, etc."""
        tool_providers: list[ToolProvider] = []
        if self._agent_registry:
            self._agent_registry.clear()
        scheduler_manager = SchedulerManager(state=self._state, router=router)
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            manifest = self._get_manifest(ext_id)
            if isinstance(ext, ToolProvider):
                tool_providers.append(ext)
            if isinstance(ext, AgentProvider) and manifest:
                if ext_id != self._settings.default_agent:
                    self._register_static_agent(ext_id, ext, manifest)
            if isinstance(ext, ChannelProvider):
                router.register_channel(ext_id, ext)
            if isinstance(ext, SchedulerProvider) and manifest:
                scheduler_manager.register(ext_id, ext, manifest)

        channel_ids = {
            eid for eid, e in self._extensions.items() if isinstance(e, ChannelProvider)
        }
        channel_descriptions = {m.id: m.name for m in manifests if m.id in channel_ids}
        router.set_channel_descriptions(channel_descriptions)
        return tool_providers, scheduler_manager

    async def update_setup_providers(self, setup_providers: dict[str, bool]) -> None:
        """Call on_setup_complete for each SetupProvider; store configured state."""
        setup_providers.clear()
        for ext_id, ext in self._extensions.items():
            if self._state.get(ext_id) == ExtensionState.ERROR:
                continue
            if not isinstance(ext, SetupProvider):
                continue
            try:
                ok, _msg = await ext.on_setup_complete()
                setup_providers[ext_id] = ok
            except Exception as e:
                logger.warning(
                    "SetupProvider %s on_setup_complete failed: %s", ext_id, e
                )
                setup_providers[ext_id] = False
