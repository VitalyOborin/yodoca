"""Natural-language capabilities summary for the orchestrator prompt."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from core.extensions.contract import Extension, ExtensionState
from core.extensions.loader.mcp_collector import McpCollector
from core.extensions.manifest import ExtensionManifest
from core.extensions.manifest_utils import iter_active_manifests
from core.settings_models import AppSettings

if TYPE_CHECKING:
    from core.agents.registry import AgentRegistry


class CapabilitiesSummaryBuilder:
    """Builds get_capabilities_summary text from loader state."""

    def __init__(
        self,
        state: dict[str, ExtensionState],
        extensions: dict[str, Extension],
        settings: AppSettings,
        agent_registry: "AgentRegistry | None",
        mcp_collector: McpCollector,
    ) -> None:
        self._state = state
        self._extensions = extensions
        self._settings = settings
        self._agent_registry = agent_registry
        self._mcp_collector = mcp_collector

    def _collect_tool_agent_parts(
        self, manifests: list[ExtensionManifest]
    ) -> list[str]:
        tool_parts: list[str] = []
        for ext_id, manifest in iter_active_manifests(manifests, self._state):
            if ext_id not in self._extensions:
                continue
            if not manifest.description:
                continue
            if ext_id == self._settings.default_agent:
                continue
            is_agent = (
                self._agent_registry is not None
                and self._agent_registry.get(ext_id) is not None
            )
            if is_agent:
                continue
            tool_parts.append(f"- {ext_id}: {manifest.description.strip()}")
        return tool_parts

    def _collect_setup_sections(
        self,
        get_manifest: Callable[[str], ExtensionManifest | None],
        setup_providers: dict[str, bool],
    ) -> list[str]:
        parts: list[str] = []
        for ext_id, is_configured in setup_providers.items():
            if is_configured:
                continue
            manifest = get_manifest(ext_id)
            if not manifest or not manifest.setup_instructions.strip():
                continue
            parts.append(f"- {ext_id}: {manifest.setup_instructions.strip()}")
        return parts

    def build(
        self,
        manifests: list[ExtensionManifest],
        get_manifest: Callable[[str], ExtensionManifest | None],
        setup_providers: dict[str, bool],
    ) -> str:
        tool_parts = self._collect_tool_agent_parts(manifests)
        setup_parts = self._collect_setup_sections(get_manifest, setup_providers)
        mcp_aliases = self._mcp_collector.collect_mcp_aliases()
        sections: list[str] = []
        if setup_parts:
            sections.append("Extensions needing setup:\n" + "\n".join(setup_parts))
        if tool_parts:
            sections.append("Available tools:\n" + "\n".join(tool_parts))
        if self._agent_registry and self._agent_registry.list_agents():
            sections.append(
                "Agent delegation:\n"
                "Use list_agents to discover available specialized agents.\n"
                "Use delegate_task to assign work to an agent."
            )
        if mcp_aliases:
            mcp_lines = "\n".join(f"- {a}" for a in mcp_aliases)
            sections.append("MCP servers:\n" + mcp_lines)
        return "\n\n".join(sections) if sections else "No extensions loaded."
