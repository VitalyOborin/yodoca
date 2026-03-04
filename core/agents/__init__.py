"""Orchestrator: AI agent application based on OpenAI Agents SDK."""

from core.agents.factory import AgentFactory, AgentSpec
from core.agents.registry import AgentRecord, AgentRegistry

__version__ = "1.0.0"
__all__ = ["AgentFactory", "AgentRecord", "AgentRegistry", "AgentSpec"]
