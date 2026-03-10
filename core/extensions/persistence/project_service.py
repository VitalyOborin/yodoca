"""Application service for project operations over sessions."""

import uuid
from typing import Any

from core.extensions.persistence.models import ProjectInfo, SessionInfo
from core.extensions.persistence.project_repository import ProjectRepository
from core.extensions.persistence.session_repository import SessionRepository
from core.extensions.update_fields import UNSET, UnsetType


class ProjectService:
    """Coordinates project persistence and session binding rules."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        session_repository: SessionRepository,
    ) -> None:
        self._projects = project_repository
        self._sessions = session_repository

    def create_project(
        self,
        *,
        name: str,
        instructions: str | None,
        agent_config: dict[str, Any] | None,
        files: list[str] | None,
        now_ts: int,
        project_id: str | None = None,
    ) -> ProjectInfo:
        return self._projects.create_project(
            project_id=project_id or f"proj_{uuid.uuid4().hex}",
            name=name,
            instructions=instructions,
            agent_config=agent_config,
            files=files or [],
            now_ts=now_ts,
        )

    def list_projects(self) -> list[ProjectInfo]:
        return self._projects.list_projects()

    def get_project(self, project_id: str) -> ProjectInfo | None:
        return self._projects.get_project(project_id)

    def update_project(
        self,
        project_id: str,
        *,
        name: str | UnsetType = UNSET,
        instructions: str | None | UnsetType = UNSET,
        agent_config: dict[str, Any] | None | UnsetType = UNSET,
        files: list[str] | UnsetType = UNSET,
        now_ts: int,
    ) -> ProjectInfo | None:
        return self._projects.update_project(
            project_id,
            name=name,
            instructions=instructions,
            agent_config=agent_config,
            files=files,
            now_ts=now_ts,
        )

    def delete_project(self, project_id: str) -> bool:
        return self._projects.delete_project(project_id)

    def bind_session(
        self, session_id: str, project_id: str | None
    ) -> SessionInfo | None:
        if project_id is not None and self._projects.get_project(project_id) is None:
            raise ValueError(f"Project {project_id} not found")
        return self._sessions.update_session(session_id, project_id=project_id)

    def get_project_instructions(self, session_id: str) -> str | None:
        session = self._sessions.get_session(session_id, include_archived=True)
        if session is None or not session.project_id:
            return None
        project = self._projects.get_project(session.project_id)
        if project is None:
            return None
        instructions = project.instructions
        if isinstance(instructions, str) and instructions.strip():
            return instructions
        return None
