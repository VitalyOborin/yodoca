"""Persistent thread metadata and history stored alongside agent_messages."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.extensions.persistence.models import ThreadInfo
from core.extensions.persistence.schema import ensure_thread_schema
from core.extensions.update_fields import UNSET, UnsetType


class ThreadRepository:
    """CRUD for thread metadata and history."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        ensure_thread_schema(db_path)

    @property
    def db_path(self) -> str:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _agent_messages_thread_column(self, conn: sqlite3.Connection) -> str:
        """Return FK column name used by agent_messages for thread identity."""
        rows = conn.execute("PRAGMA table_info(agent_messages)").fetchall()
        cols = {str(row["name"]) for row in rows}
        if "session_id" in cols:
            return "session_id"
        if "thread_id" in cols:
            return "thread_id"
        return "session_id"

    def create_thread(
        self,
        thread_id: str,
        channel_id: str,
        project_id: str | None,
        title: str | None,
        now_ts: int,
    ) -> ThreadInfo:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO threads (
                    thread_id,
                    project_id,
                    title,
                    channel_id,
                    created_at,
                    last_active_at,
                    is_archived
                )
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(thread_id) DO UPDATE SET
                    project_id = COALESCE(excluded.project_id, threads.project_id),
                    title = COALESCE(excluded.title, threads.title),
                    channel_id = CASE
                        WHEN threads.channel_id = 'unknown' THEN excluded.channel_id
                        ELSE threads.channel_id
                    END,
                    last_active_at = MAX(
                        threads.last_active_at,
                        excluded.last_active_at
                    )
                """,
                (thread_id, project_id, title, channel_id, now_ts, now_ts),
            )
            conn.commit()
        thread = self.get_thread(thread_id, include_archived=True)
        if thread is None:
            raise RuntimeError(f"Failed to persist thread {thread_id}")
        return thread

    def get_thread(
        self, thread_id: str, include_archived: bool = False
    ) -> ThreadInfo | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    thread_id,
                    project_id,
                    title,
                    channel_id,
                    created_at,
                    last_active_at,
                    is_archived
                FROM threads
                WHERE thread_id = ?
                  AND (? = 1 OR is_archived = 0)
                """,
                (thread_id, int(include_archived)),
            ).fetchone()
        return self._row_to_thread(row)

    def list_threads(
        self,
        include_archived: bool = False,
        project_id: str | None = None,
        channel_id: str | None = None,
    ) -> list[ThreadInfo]:
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
                    thread_id,
                    project_id,
                    title,
                    channel_id,
                    created_at,
                    last_active_at,
                    is_archived
                FROM threads
                WHERE {where}
                ORDER BY last_active_at DESC, created_at DESC, thread_id DESC
                """,
                params,
            ).fetchall()
        threads: list[ThreadInfo] = []
        for row in rows:
            thread = self._row_to_thread(row)
            if thread is not None:
                threads.append(thread)
        return threads

    def update_thread(
        self,
        thread_id: str,
        *,
        title: str | None | UnsetType = UNSET,
        project_id: str | None | UnsetType = UNSET,
        is_archived: bool | UnsetType = UNSET,
        last_active_at: int | UnsetType = UNSET,
        channel_id: str | UnsetType = UNSET,
    ) -> ThreadInfo | None:
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
            return self.get_thread(thread_id, include_archived=True)
        params.append(thread_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE threads SET {', '.join(assignments)} WHERE thread_id = ?",
                params,
            )
            conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get_thread(thread_id, include_archived=True)

    def archive_thread(self, thread_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE threads SET is_archived = 1 WHERE thread_id = ?",
                (thread_id,),
            )
            conn.commit()
        return cur.rowcount > 0

    def get_thread_history(self, thread_id: str) -> list[dict[str, Any]] | None:
        if self.get_thread(thread_id, include_archived=True) is None:
            return None
        if not Path(self._db_path).exists():
            return []
        with self._connect() as conn:
            key_col = self._agent_messages_thread_column(conn)
            rows = conn.execute(
                f"""
                SELECT message_data, created_at
                FROM agent_messages
                WHERE {key_col} = ?
                ORDER BY id ASC
                """,
                (thread_id,),
            ).fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            try:
                parsed = json.loads(row["message_data"])
            except json.JSONDecodeError:
                parsed = {"raw": row["message_data"]}
            if isinstance(parsed, dict):
                if "created_at" not in parsed:
                    raw_created_at = row["created_at"]
                    if raw_created_at is not None:
                        try:
                            parsed["created_at"] = self._parse_created_at(raw_created_at)
                        except (ValueError, TypeError):
                            pass
                history.append(parsed)
            else:
                history.append({"value": parsed})
        return history

    def sync_last_active_at(self, thread_id: str) -> int | None:
        with self._connect() as conn:
            key_col = self._agent_messages_thread_column(conn)
            row = conn.execute(
                f"""
                SELECT COALESCE(
                    (
                        SELECT CAST(strftime('%s', MAX(created_at)) AS INTEGER)
                        FROM agent_messages
                        WHERE {key_col} = ?
                    ),
                    (
                        SELECT CAST(strftime('%s', updated_at) AS INTEGER)
                        FROM threads
                        WHERE thread_id = ?
                    )
                ) AS last_active_at
                """,
                (thread_id, thread_id),
            ).fetchone()
            if row is None or row["last_active_at"] is None:
                return None
            last_active_at = int(row["last_active_at"])
            conn.execute(
                "UPDATE threads SET last_active_at = ? WHERE thread_id = ?",
                (last_active_at, thread_id),
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
            return int(dt.replace(tzinfo=UTC).timestamp())

    def _row_to_thread(self, row: sqlite3.Row | None) -> ThreadInfo | None:
        if row is None:
            return None
        return ThreadInfo(
            id=row["thread_id"],
            project_id=row["project_id"],
            title=row["title"],
            channel_id=row["channel_id"],
            created_at=self._parse_created_at(row["created_at"]),
            last_active_at=int(row["last_active_at"]),
            is_archived=bool(row["is_archived"]),
        )

