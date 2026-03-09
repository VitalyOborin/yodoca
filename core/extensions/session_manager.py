"""SessionManager: session lifecycle, rotation, and timeout handling."""

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
