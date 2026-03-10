"""SessionManager: runtime SQLiteSession lifecycle backed by persistent metadata."""

import asyncio
import time
from typing import Any

from core.events.topics import SystemTopics
from core.extensions.session_repository import SessionRepository
from core.extensions.update_fields import UNSET


class SessionManager:
    """Owns main/background runtime sessions and inactivity-based rotation."""

    def __init__(self) -> None:
        self._session: Any = None
        self._session_id: str | None = None
        self._last_message_at: float | None = None
        self._session_timeout: int = 1800
        self._session_db_path: str | None = None
        self._event_bus: Any = None
        self._session_pool: dict[str, Any] = {}
        self._repository: SessionRepository | None = None

    @property
    def session(self) -> Any:
        return self._session

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def session_repository(self) -> SessionRepository:
        if self._repository is None:
            raise RuntimeError("Session repository is not configured")
        return self._repository

    def set_session(self, session: Any, session_id: str) -> None:
        self._session = session
        self._session_id = session_id
        self._session_pool[session_id] = session
        self._persist_session(session_id, channel_id="unknown")

    def get_or_create_session(self, session_id: str, channel_id: str) -> Any:
        """Get or create a session from the pool keyed by session_id."""
        if self._session_db_path is None:
            raise RuntimeError(
                "Session not configured: call configure_session before invoke"
            )
        if session_id not in self._session_pool:
            self._persist_session(session_id, channel_id=channel_id)
            from core.extensions.session_sqlite import UnicodeSQLiteSession

            self._session_pool[session_id] = UnicodeSQLiteSession(
                session_id, self._session_db_path, sessions_table="sessions"
            )
        else:
            self._persist_session(session_id, channel_id=channel_id)
        return self._session_pool[session_id]

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
        self._repository = SessionRepository(session_db_path)
        ts = now_ts if now_ts is not None else time.time()
        self._session_id = f"orchestrator_{int(ts)}"
        self._persist_session(self._session_id, channel_id="unknown", now_ts=int(ts))
        from core.extensions.session_sqlite import UnicodeSQLiteSession

        self._session = UnicodeSQLiteSession(
            self._session_id, session_db_path, sessions_table="sessions"
        )
        self._session_pool[self._session_id] = self._session

    async def rotate_session(self, now_ts: float | None = None) -> None:
        old_id = self._session_id
        ts = now_ts if now_ts is not None else time.time()
        self._session_id = f"orchestrator_{int(ts)}"
        if self._session_db_path is None:
            raise RuntimeError(
                "Session not configured: call configure_session before invoke"
            )
        self._persist_session(self._session_id, channel_id="unknown", now_ts=int(ts))
        from core.extensions.session_sqlite import UnicodeSQLiteSession

        self._session = UnicodeSQLiteSession(
            self._session_id, self._session_db_path, sessions_table="sessions"
        )
        self._session_pool[self._session_id] = self._session
        if self._event_bus and old_id:
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

    def touch_session(
        self,
        session_id: str,
        *,
        channel_id: str,
        now_ts: int | None = None,
    ) -> dict[str, Any] | None:
        repo = self.session_repository
        effective_now = now_ts if now_ts is not None else int(time.time())
        session = repo.get_session(session_id, include_archived=True)
        if session is None:
            return repo.create_session(
                session_id=session_id,
                channel_id=channel_id,
                project_id=None,
                title=None,
                now_ts=effective_now,
            )
        return repo.update_session(
            session_id,
            channel_id=channel_id,
            last_active_at=effective_now,
            is_archived=False,
        )

    async def list_sessions(
        self,
        include_archived: bool = False,
        project_id: str | None = None,
        channel_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.session_repository.list_sessions,
            include_archived,
            project_id,
            channel_id,
        )

    async def get_session(
        self, session_id: str, include_archived: bool = False
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.session_repository.get_session,
            session_id,
            include_archived,
        )

    async def archive_session(self, session_id: str) -> bool:
        return await asyncio.to_thread(
            self.session_repository.archive_session, session_id
        )

    async def update_session(
        self,
        session_id: str,
        *,
        title: str | None | object = UNSET,
        project_id: str | None | object = UNSET,
        is_archived: bool | object = UNSET,
        channel_id: str | object = UNSET,
        last_active_at: int | object = UNSET,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.session_repository.update_session,
            session_id,
            title=title,
            project_id=project_id,
            is_archived=is_archived,
            channel_id=channel_id,
            last_active_at=last_active_at,
        )

    async def get_session_history(self, session_id: str) -> list[dict[str, Any]] | None:
        return await asyncio.to_thread(
            self.session_repository.get_session_history,
            session_id,
        )

    async def sync_last_active_at(self, session_id: str) -> int | None:
        return await asyncio.to_thread(
            self.session_repository.sync_last_active_at,
            session_id,
        )

    def get_background_session(self, now_ts: float | None = None) -> Any:
        if self._session_db_path is None:
            return None
        ts = now_ts if now_ts is not None else time.time()
        session_id = f"background_{int(ts)}"
        self._persist_session(session_id, channel_id="unknown", now_ts=int(ts))
        from core.extensions.session_sqlite import UnicodeSQLiteSession

        return UnicodeSQLiteSession(
            session_id, self._session_db_path, sessions_table="sessions"
        )

    def _persist_session(
        self,
        session_id: str,
        *,
        channel_id: str,
        now_ts: int | None = None,
    ) -> None:
        if self._repository is None:
            return
        effective_now = now_ts if now_ts is not None else int(time.time())
        self._repository.create_session(
            session_id=session_id,
            channel_id=channel_id,
            project_id=None,
            title=None,
            now_ts=effective_now,
        )
