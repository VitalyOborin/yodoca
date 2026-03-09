"""SessionManager: session lifecycle, rotation, and timeout handling."""

import asyncio
import sqlite3
import time
from typing import Any

from core.events.topics import SystemTopics


class SessionManager:
    """Owns main/background sessions and inactivity-based rotation."""

    def __init__(self) -> None:
        self._session: Any = None
        self._session_id: str | None = None
        self._last_message_at: float | None = None
        self._session_timeout: int = 1800
        self._session_db_path: str | None = None
        self._event_bus: Any = None
        self._session_pool: dict[str, Any] = {}

    @staticmethod
    def _ensure_session_meta_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS yodoca_session_meta (
                session_id TEXT PRIMARY KEY,
                updated_at INTEGER NOT NULL
            )
            """
        )

    @staticmethod
    def _read_last_message_ts(
        conn: sqlite3.Connection, session_id: str
    ) -> int | None:
        row = conn.execute(
            """
            SELECT CAST(strftime('%s', MAX(created_at)) AS INTEGER)
            FROM agent_messages
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        value = row[0] if row else None
        return int(value) if value is not None else None

    @staticmethod
    def _read_session_fallback_ts(
        conn: sqlite3.Connection, session_id: str
    ) -> int | None:
        row = conn.execute(
            """
            SELECT CAST(strftime('%s', updated_at) AS INTEGER)
            FROM agent_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        value = row[0] if row else None
        return int(value) if value is not None else None

    @staticmethod
    def _read_meta_ts(conn: sqlite3.Connection, session_id: str) -> int | None:
        row = conn.execute(
            "SELECT updated_at FROM yodoca_session_meta WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        value = row[0] if row else None
        return int(value) if value is not None else None

    @staticmethod
    def _upsert_meta_ts(conn: sqlite3.Connection, session_id: str, updated_at: int) -> None:
        conn.execute(
            """
            INSERT INTO yodoca_session_meta (session_id, updated_at)
            VALUES (?, ?)
            ON CONFLICT(session_id)
            DO UPDATE SET updated_at = excluded.updated_at
            """,
            (session_id, updated_at),
        )

    def _sync_session_updated_at(self, conn: sqlite3.Connection, session_id: str) -> int:
        last_message_ts = self._read_last_message_ts(conn, session_id)
        fallback_ts = self._read_session_fallback_ts(conn, session_id)
        target_updated_at = last_message_ts if last_message_ts is not None else (fallback_ts or 0)

        existing = self._read_meta_ts(conn, session_id)
        if existing != target_updated_at:
            self._upsert_meta_ts(conn, session_id, target_updated_at)
        return target_updated_at

    @property
    def session(self) -> Any:
        return self._session

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def event_bus(self) -> Any:
        return self._event_bus

    def set_session(self, session: Any, session_id: str) -> None:
        self._session = session
        self._session_id = session_id

    def get_or_create_session(self, session_id: str) -> Any:
        """Get or create a session from the pool keyed by session_id."""
        if self._session_db_path is None:
            raise RuntimeError(
                "Session not configured: call configure_session before invoke"
            )
        if session_id not in self._session_pool:
            from agents import SQLiteSession

            self._session_pool[session_id] = SQLiteSession(
                session_id, self._session_db_path
            )
        return self._session_pool[session_id]

    def list_session_ids(self) -> list[str]:
        """List all session IDs in the pool (for /api/conversations)."""
        return list(self._session_pool.keys())

    def delete_session(self, session_id: str) -> bool:
        """Remove a session from the pool. Returns True if it existed."""
        if session_id in self._session_pool:
            del self._session_pool[session_id]
            return True
        return False

    async def get_session_items(self, session_id: str) -> list[dict[str, Any]] | None:
        """Return stored session items for an existing pooled session."""
        session = self._session_pool.get(session_id)
        if session is None:
            return None
        items = await session.get_items()
        return [item for item in items if isinstance(item, dict)]

    async def get_session_updated_at(self, session_id: str) -> int | None:
        """Return persisted integer updated_at for a pooled session."""
        if session_id not in self._session_pool:
            return None
        if self._session_db_path is None:
            return None

        def _read() -> int:
            with sqlite3.connect(self._session_db_path) as conn:
                self._ensure_session_meta_table(conn)
                updated_at = self._sync_session_updated_at(conn, session_id)
                conn.commit()
                return updated_at

        return await asyncio.to_thread(_read)

    async def list_session_summaries(self) -> list[dict[str, Any]]:
        """List pooled sessions with stable integer updated_at."""
        session_ids = list(self._session_pool.keys())
        if not session_ids:
            return []
        if self._session_db_path is None:
            return [{"id": sid, "updated_at": 0} for sid in session_ids]

        def _read() -> list[dict[str, Any]]:
            with sqlite3.connect(self._session_db_path) as conn:
                self._ensure_session_meta_table(conn)
                summaries: list[dict[str, Any]] = []
                for sid in session_ids:
                    updated_at = self._sync_session_updated_at(conn, sid)
                    summaries.append({"id": sid, "updated_at": updated_at})
                conn.commit()
                return summaries

        return await asyncio.to_thread(_read)

    def configure_session(
        self,
        session_db_path: str,
        session_timeout: int,
        event_bus: Any = None,
        now_ts: float | None = None,
    ) -> None:
        self._session_db_path = session_db_path
        self._session_timeout = session_timeout
        self._event_bus = event_bus
        ts = now_ts if now_ts is not None else time.time()
        self._session_id = f"orchestrator_{int(ts)}"
        from agents import SQLiteSession

        self._session = SQLiteSession(self._session_id, session_db_path)

    async def rotate_session(self, now_ts: float | None = None) -> None:
        old_id = self._session_id
        ts = now_ts if now_ts is not None else time.time()
        self._session_id = f"orchestrator_{int(ts)}"
        from agents import SQLiteSession

        if self._session_db_path is None:
            raise RuntimeError(
                "Session not configured: call configure_session before invoke"
            )
        self._session = SQLiteSession(self._session_id, self._session_db_path)
        if self._event_bus:
            await self._event_bus.publish(
                SystemTopics.SESSION_COMPLETED,
                "kernel",
                {"session_id": old_id, "reason": "inactivity_timeout"},
            )

    async def maybe_rotate(self, now_ts: float | None = None) -> None:
        now = now_ts if now_ts is not None else time.time()
        if (
            self._last_message_at is not None
            and (now - self._last_message_at) > self._session_timeout
        ):
            await self.rotate_session(now_ts=now)
        self._last_message_at = now

    def get_background_session(self, now_ts: float | None = None) -> Any:
        if self._session_db_path is None:
            return None
        from agents import SQLiteSession

        ts = now_ts if now_ts is not None else time.time()
        session_id = f"background_{int(ts)}"
        return SQLiteSession(session_id, self._session_db_path)
