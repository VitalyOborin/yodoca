"""Pydantic models for config/settings.yaml (validated after YAML merge)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class SupervisorSettings(BaseModel):
    restart_file: str = "sandbox/.restart_requested"
    restart_file_check_interval: int = 5


class AgentEntry(BaseModel):
    provider: str = ""
    model: str = ""
    instructions: str = ""
    temperature: float = 0.7
    max_tokens: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ProviderEntry(BaseModel):
    type: str = "openai_compatible"
    api_mode: str = "responses"
    base_url: str | None = None
    api_key_secret: str | None = None
    api_key_literal: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)
    supports_hosted_tools: bool = True


class EventBusSettings(BaseModel):
    db_path: str = "sandbox/data/event_journal.db"
    poll_interval: float = 5.0
    batch_size: int = 3
    max_retries: int = 3
    busy_timeout: int = 5000
    stale_timeout: int = 300


class LoggingSettings(BaseModel):
    file: str = "sandbox/logs/app.log"
    level: str = "INFO"
    console_level: str | None = None
    console_style: Literal["text", "json"] = "text"
    file_style: Literal["text", "json"] = "text"
    log_to_console: bool = False
    max_bytes: int = 10485760
    backup_count: int = 3
    subsystems: dict[str, str] = Field(default_factory=dict)
    console_subsystems: list[str] = Field(default_factory=list)


class ThreadSettings(BaseModel):
    timeout_sec: int = 1800


class ModelCatalogEntry(BaseModel):
    cost_tier: str = "medium"
    capability_tier: str = "standard"
    strengths: list[str] = Field(default_factory=list)
    context_window: int = 128000


def _default_agents() -> dict[str, AgentEntry]:
    return {"default": AgentEntry(provider="openai", model="gpt-5")}


class AppSettings(BaseModel):
    """Top-level application settings (config/settings.yaml)."""

    default_agent: str = "orchestrator_agent"
    supervisor: SupervisorSettings = Field(default_factory=SupervisorSettings)
    agents: dict[str, AgentEntry] = Field(default_factory=_default_agents)
    providers: dict[str, ProviderEntry] = Field(default_factory=dict)
    event_bus: EventBusSettings = Field(default_factory=EventBusSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    thread: ThreadSettings = Field(default_factory=ThreadSettings)
    extensions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    models: dict[str, ModelCatalogEntry] = Field(default_factory=dict)
