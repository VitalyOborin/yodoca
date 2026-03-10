"""Persistent session metadata and history stored alongside agent_messages."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from core.extensions.update_fields import UNSET


class SessionRepository:
    """Owns schema bootstrap, migration, and CRUD for session metadata."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_schema()

    @property
    def db_path(self) -> str:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    instructions TEXT,
                    agent_config TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS project_files (
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    file_path TEXT NOT NULL,
                    added_at INTEGER NOT NULL,
                    PRIMARY KEY (project_id, file_path)
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    title TEXT,
                    channel_id TEXT NOT NULL DEFAULT 'unknown',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active_at INTEGER NOT NULL DEFAULT 0,
                    is_archived INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.commit()

    def create_session(
        self,
        session_id: str,
        channel_id: str,
        project_id: str | None,
        title: str | None,
        now_ts: int,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    project_id,
                    title,
                    channel_id,
                    created_at,
                    last_active_at,
                    is_archived
                )
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(session_id) DO UPDATE SET
                    project_id = COALESCE(excluded.project_id, sessions.project_id),
                    title = COALESCE(excluded.title, sessions.title),
                    channel_id = CASE
                        WHEN sessions.channel_id = 'unknown' THEN excluded.channel_id
                        ELSE sessions.channel_id
                    END,
                    last_active_at = MAX(
                        sessions.last_active_at,
                        excluded.last_active_at
                    )
                """,
                (session_id, project_id, title, channel_id, now_ts, now_ts),
            )
            conn.commit()
        session = self.get_session(session_id, include_archived=True)
        if session is None:
            raise RuntimeError(f"Failed to persist session {session_id}")
        return session

    def get_session(
        self, session_id: str, include_archived: bool = False
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    session_id,
                    project_id,
                    title,
                    channel_id,
                    created_at,
                    last_active_at,
                    is_archived
                FROM sessions
                WHERE session_id = ?
                  AND (? = 1 OR is_archived = 0)
                """,
                (session_id, int(include_archived)),
            ).fetchone()
        return self._row_to_session(row)

    def list_sessions(
        self,
        include_archived: bool = False,
        project_id: str | None = None,
        channel_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["(? = 1 OR is_archived = 0)"]
        params: list[Any] = [int(include_archived)]
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(channel_id)
        where = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    session_id,
                    project_id,
                    title,
                    channel_id,
                    created_at,
                    last_active_at,
                    is_archived
                FROM sessions
                WHERE {where}
                ORDER BY last_active_at DESC, created_at DESC, session_id DESC
                """,
                params,
            ).fetchall()
        return [self._row_to_session(row) for row in rows if row is not None]

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None | object = UNSET,
        project_id: str | None | object = UNSET,
        is_archived: bool | object = UNSET,
        last_active_at: int | object = UNSET,
        channel_id: str | object = UNSET,
    ) -> dict[str, Any] | None:
        assignments: list[str] = []
        params: list[Any] = []
        if title is not UNSET:
            assignments.append("title = ?")
            params.append(title)
        if project_id is not UNSET:
            assignments.append("project_id = ?")
            params.append(project_id)
        if is_archived is not UNSET:
            assignments.append("is_archived = ?")
            params.append(int(bool(is_archived)))
        if last_active_at is not UNSET:
            assignments.append("last_active_at = ?")
            params.append(int(last_active_at))
        if channel_id is not UNSET:
            assignments.append("channel_id = ?")
            params.append(channel_id)
        if not assignments:
            return self.get_session(session_id, include_archived=True)
        params.append(session_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE sessions SET {', '.join(assignments)} WHERE session_id = ?",
                params,
            )
            conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get_session(session_id, include_archived=True)

    def archive_session(self, session_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sessions SET is_archived = 1 WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
        return cur.rowcount > 0

    def get_session_history(self, session_id: str) -> list[dict[str, Any]] | None:
        if self.get_session(session_id, include_archived=True) is None:
            return None
        if not Path(self._db_path).exists():
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_data
                FROM agent_messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            try:
                parsed = json.loads(row["message_data"])
            except json.JSONDecodeError:
                parsed = {"raw": row["message_data"]}
            if isinstance(parsed, dict):
                history.append(parsed)
            else:
                history.append({"value": parsed})
        return history

    def sync_last_active_at(self, session_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(
                    (
                        SELECT CAST(strftime('%s', MAX(created_at)) AS INTEGER)
                        FROM agent_messages
                        WHERE session_id = ?
                    ),
                    (
                        SELECT CAST(strftime('%s', updated_at) AS INTEGER)
                        FROM sessions
                        WHERE session_id = ?
                    )
                ) AS last_active_at
                """,
                (session_id, session_id),
            ).fetchone()
            if row is None or row["last_active_at"] is None:
                return None
            last_active_at = int(row["last_active_at"])
            conn.execute(
                "UPDATE sessions SET last_active_at = ? WHERE session_id = ?",
                (last_active_at, session_id),
            )
            conn.commit()
        return last_active_at

    def _parse_created_at(self, value: Any) -> int:
        """Parse created_at from epoch int or ISO timestamp string."""
        if isinstance(value, (int, float)):
            return int(value)
        try:
            return int(value)
        except (ValueError, TypeError):
            dt = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
            return int(dt.replace(tzinfo=datetime.UTC).timestamp())

    def _row_to_session(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row["session_id"],
            "project_id": row["project_id"],
            "title": row["title"],
            "channel_id": row["channel_id"],
            "created_at": self._parse_created_at(row["created_at"]),
            "last_active_at": int(row["last_active_at"]),
            "is_archived": bool(row["is_archived"]),
        }
