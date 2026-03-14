"""Typed domain models for thread and project persistence."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ThreadInfo:
    """Stored thread metadata."""

    id: str
    project_id: str | None
    title: str | None
    title_source: str | None
    title_status: str | None
    title_updated_at: int | None
    channel_id: str
    created_at: int
    last_active_at: int
    is_archived: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "title_source": self.title_source,
            "title_status": self.title_status,
            "title_updated_at": self.title_updated_at,
            "channel_id": self.channel_id,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "is_archived": self.is_archived,
        }


@dataclass(frozen=True)
class ProjectInfo:
    """Stored project metadata and attached files."""

    id: str
    name: str
    description: str | None
    icon: str | None
    instructions: str | None
    agent_config: dict[str, Any]
    created_at: int
    updated_at: int
    files: list[str]
    links: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "instructions": self.instructions,
            "agent_config": self.agent_config,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "files": list(self.files),
            "links": list(self.links),
        }

