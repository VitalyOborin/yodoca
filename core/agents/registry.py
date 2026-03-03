"""Agent Registry: central repository of available agents.

Populated by Loader, queried by Orchestrator tools.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from core.extensions.contract import (
    AgentInvocationContext,
    AgentProvider,
    AgentResponse,
)
from core.extensions.manifest import AgentLimits


@dataclass(frozen=True)
class AgentRecord:
    """Agent metadata in the registry. Discovery-oriented, not execution-oriented."""

    id: str
    name: str
    description: str
    model: str | None = None
    integration_mode: Literal["tool", "handoff"] = "tool"
    tools: list[str] = field(default_factory=list)
    limits: AgentLimits | None = None
    source: Literal["static", "dynamic"] = "static"
    expires_at: datetime | None = None


class AgentRegistry:
    """Central registry of available agents.

    Populated by Loader, queried by Orchestrator tools.
    """

    def __init__(self) -> None:
        self._records: dict[str, AgentRecord] = {}
        self._providers: dict[str, AgentProvider] = {}
        self._active: dict[str, int] = {}

    def register(self, record: AgentRecord, provider: AgentProvider) -> None:
        """Register an agent. Called by Loader for each AgentProvider extension."""
        self._records[record.id] = record
        self._providers[record.id] = provider
        self._active[record.id] = 0

    def unregister(self, agent_id: str) -> None:
        """Remove an agent from the registry."""
        self._records.pop(agent_id, None)
        self._providers.pop(agent_id, None)
        self._active.pop(agent_id, None)

    def clear(self) -> None:
        """Remove all agents from the registry. Used by Loader before repopulating."""
        self._records.clear()
        self._providers.clear()
        self._active.clear()

    def get(self, agent_id: str) -> tuple[AgentRecord, AgentProvider] | None:
        """Get agent record and provider by id."""
        record = self._records.get(agent_id)
        provider = self._providers.get(agent_id)
        if record is not None and provider is not None:
            return (record, provider)
        return None

    def list_agents(self, available_only: bool = False) -> list[AgentRecord]:
        """List all registered agents, optionally filtering by availability."""
        records = list(self._records.values())
        if available_only:
            records = [r for r in records if self._active.get(r.id, 0) == 0]
        return records

    async def invoke(
        self,
        agent_id: str,
        task: str,
        context: AgentInvocationContext | None = None,
    ) -> AgentResponse:
        """Resolve agent by id and invoke. Tracks active invocations."""
        pair = self.get(agent_id)
        if pair is None:
            return AgentResponse(
                status="error",
                content="",
                error=f"Unknown agent: {agent_id}",
            )
        record, provider = pair
        self._active[agent_id] = self._active.get(agent_id, 0) + 1
        try:
            return await provider.invoke(task, context)
        finally:
            self._active[agent_id] = max(0, self._active.get(agent_id, 1) - 1)

    def is_busy(self, agent_id: str) -> bool:
        """Return True if the agent is currently executing an invocation."""
        return self._active.get(agent_id, 0) > 0

    def cleanup_expired(self) -> int:
        """Unregister dynamic agents whose expires_at has passed.

        Returns the number of agents removed.
        """
        now = datetime.now(UTC)
        to_remove = [
            r.id
            for r in self._records.values()
            if r.source == "dynamic" and r.expires_at is not None and r.expires_at < now
        ]
        for agent_id in to_remove:
            self.unregister(agent_id)
        return len(to_remove)
