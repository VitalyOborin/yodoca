"""Project persistence stored in thread.db alongside thread metadata."""

import json
import sqlite3
from typing import Any

from core.extensions.persistence.models import ProjectInfo
from core.extensions.persistence.schema import ensure_thread_schema
from core.extensions.update_fields import UNSET, UnsetType


class ProjectRepository:
    """CRUD for projects and attached file paths."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        ensure_thread_schema(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def create_project(
        self,
        *,
        project_id: str,
        name: str,
        instructions: str | None,
        agent_config: dict[str, Any] | None,
        files: list[str],
        now_ts: int,
    ) -> ProjectInfo:
        payload = json.dumps(agent_config or {}, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, name, instructions, agent_config, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, name, instructions, payload, now_ts, now_ts),
            )
            self._replace_files(conn, project_id=project_id, files=files, now_ts=now_ts)
            conn.commit()
        project = self.get_project(project_id)
        if project is None:
            raise RuntimeError(f"Failed to persist project {project_id}")
        return project

    def get_project(self, project_id: str) -> ProjectInfo | None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.instructions,
                    p.agent_config,
                    p.created_at,
                    p.updated_at,
                    pf.file_path
                FROM projects AS p
                LEFT JOIN project_files AS pf
                    ON pf.project_id = p.id
                WHERE p.id = ?
                ORDER BY pf.added_at ASC, pf.file_path ASC
                """,
                (project_id,),
            ).fetchall()
        projects = self._rows_to_projects(rows)
        return projects[0] if projects else None

    def list_projects(self) -> list[ProjectInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.instructions,
                    p.agent_config,
                    p.created_at,
                    p.updated_at,
                    pf.file_path
                FROM projects AS p
                LEFT JOIN project_files AS pf
                    ON pf.project_id = p.id
                ORDER BY p.updated_at DESC, p.created_at DESC, p.id DESC
                """
            ).fetchall()
        return self._rows_to_projects(rows)

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
        if (
            name is UNSET
            and instructions is UNSET
            and agent_config is UNSET
            and files is UNSET
        ):
            return self.get_project(project_id)

        assignments: list[str] = ["updated_at = ?"]
        params: list[Any] = [now_ts]
        if name is not UNSET:
            assignments.append("name = ?")
            params.append(name)
        if instructions is not UNSET:
            assignments.append("instructions = ?")
            params.append(instructions)
        if agent_config is not UNSET:
            assignments.append("agent_config = ?")
            params.append(json.dumps(agent_config or {}, ensure_ascii=False))
        params.append(project_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE projects SET {', '.join(assignments)} WHERE id = ?",
                params,
            )
            if cur.rowcount == 0:
                conn.rollback()
                return None
            if files is not UNSET:
                self._replace_files(
                    conn,
                    project_id=project_id,
                    files=files,
                    now_ts=now_ts,
                )
            conn.commit()
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.commit()
        return cur.rowcount > 0

    def _replace_files(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        files: list[str],
        now_ts: int,
    ) -> None:
        conn.execute("DELETE FROM project_files WHERE project_id = ?", (project_id,))
        for file_path in files:
            conn.execute(
                """
                INSERT INTO project_files (project_id, file_path, added_at)
                VALUES (?, ?, ?)
                """,
                (project_id, file_path, now_ts),
            )

    def _rows_to_projects(self, rows: list[sqlite3.Row]) -> list[ProjectInfo]:
        projects: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for row in rows:
            project_id = row["id"]
            if project_id not in projects:
                projects[project_id] = {
                    "id": project_id,
                    "name": row["name"],
                    "instructions": row["instructions"],
                    "agent_config": json.loads(row["agent_config"] or "{}"),
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                    "files": [],
                }
                order.append(project_id)
            file_path = row["file_path"]
            if isinstance(file_path, str):
                projects[project_id]["files"].append(file_path)
        return [
            ProjectInfo(
                id=projects[project_id]["id"],
                name=projects[project_id]["name"],
                instructions=projects[project_id]["instructions"],
                agent_config=projects[project_id]["agent_config"],
                created_at=projects[project_id]["created_at"],
                updated_at=projects[project_id]["updated_at"],
                files=projects[project_id]["files"],
            )
            for project_id in order
        ]
