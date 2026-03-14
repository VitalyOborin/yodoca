"""ThreadManager: runtime SQLiteSession lifecycle backed by persistent metadata."""

import asyncio
import time
from typing import Any

from core.events.topics import SystemTopics
from core.extensions.persistence.models import ThreadInfo
from core.extensions.persistence.session_sqlite import UnicodeSQLiteSession
from core.extensions.persistence.thread_repository import ThreadRepository
from core.extensions.update_fields import UNSET, UnsetType


class ThreadManager:
    """Owns main/background runtime threads and inactivity-based rotation.

    NOTE: OpenAI Agents SDK stores conversation key as `session_id`.
    We pass `thread_id` into `UnicodeSQLiteSession(...)`; inside SDK it remains
    `session_id` and is persisted into SDK-owned tables.
    """

    def __init__(self) -> None:
        self._thread: Any = None
        self._thread_id: str | None = None
        self._last_message_at: float | None = None
        self._thread_timeout: int = 1800
        self._thread_db_path: str | None = None
        self._event_bus: Any = None
        self._thread_pool: dict[str, Any] = {}
        self._repository: ThreadRepository | None = None

    @property
    def thread(self) -> Any:
        return self._thread

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    @property
    def thread_repository(self) -> ThreadRepository:
        if self._repository is None:
            raise RuntimeError("Thread repository is not configured")
        return self._repository

    def set_thread(self, thread: Any, thread_id: str) -> None:
        self._thread = thread
        self._thread_id = thread_id
        self._thread_pool[thread_id] = thread
        self._persist_thread(thread_id, channel_id="unknown")

    def get_or_create_thread(self, thread_id: str, channel_id: str) -> Any:
        """Get or create a thread from the pool keyed by thread_id."""
        if self._thread_db_path is None:
            raise RuntimeError(
                "Thread not configured: call configure_thread before invoke"
            )
        if thread_id not in self._thread_pool:
            self._persist_thread(thread_id, channel_id=channel_id)
            self._thread_pool[thread_id] = UnicodeSQLiteSession(
                thread_id, self._thread_db_path, sessions_table="sessions"
            )
        else:
            self._persist_thread(thread_id, channel_id=channel_id)
        return self._thread_pool[thread_id]

    def configure_thread(
        self,
        thread_db_path: str,
        thread_timeout: int,
        event_bus: Any = None,
        now_ts: float | None = None,
    ) -> None:
        self._thread_db_path = thread_db_path
        self._thread_timeout = thread_timeout
        self._event_bus = event_bus
        self._repository = ThreadRepository(thread_db_path)
        ts = now_ts if now_ts is not None else time.time()
        self._thread_id = f"orchestrator_{int(ts)}"
        self._persist_thread(self._thread_id, channel_id="unknown", now_ts=int(ts))

        self._thread = UnicodeSQLiteSession(
            self._thread_id, thread_db_path, sessions_table="sessions"
        )
        self._thread_pool[self._thread_id] = self._thread

    async def rotate_thread(self, now_ts: float | None = None) -> None:
        old_id = self._thread_id
        ts = now_ts if now_ts is not None else time.time()
        self._thread_id = f"orchestrator_{int(ts)}"
        if self._thread_db_path is None:
            raise RuntimeError(
                "Thread not configured: call configure_thread before invoke"
            )
        self._persist_thread(self._thread_id, channel_id="unknown", now_ts=int(ts))

        self._thread = UnicodeSQLiteSession(
            self._thread_id, self._thread_db_path, sessions_table="sessions"
        )
        self._thread_pool[self._thread_id] = self._thread
        if self._event_bus and old_id:
            await self._event_bus.publish(
                SystemTopics.THREAD_COMPLETED,
                "kernel",
                {"thread_id": old_id, "reason": "inactivity_timeout"},
            )

    async def maybe_rotate(self, now_ts: float | None = None) -> None:
        now = now_ts if now_ts is not None else time.time()
        if (
            self._last_message_at is not None
            and (now - self._last_message_at) > self._thread_timeout
        ):
            await self.rotate_thread(now_ts=now)
        self._last_message_at = now

    def touch_thread(
        self,
        thread_id: str,
        *,
        channel_id: str,
        now_ts: int | None = None,
    ) -> ThreadInfo | None:
        repo = self.thread_repository
        effective_now = now_ts if now_ts is not None else int(time.time())
        thread = repo.get_thread(thread_id, include_archived=True)
        if thread is None:
            return repo.create_thread(
                thread_id=thread_id,
                channel_id=channel_id,
                project_id=None,
                title=None,
                now_ts=effective_now,
            )
        return repo.update_thread(
            thread_id,
            channel_id=channel_id,
            last_active_at=effective_now,
            is_archived=False,
        )

    async def list_threads(
        self,
        include_archived: bool = False,
        project_id: str | None = None,
        channel_id: str | None = None,
    ) -> list[ThreadInfo]:
        return await asyncio.to_thread(
            self.thread_repository.list_threads,
            include_archived,
            project_id,
            channel_id,
        )

    async def get_thread(
        self, thread_id: str, include_archived: bool = False
    ) -> ThreadInfo | None:
        return await asyncio.to_thread(
            self.thread_repository.get_thread,
            thread_id,
            include_archived,
        )

    async def archive_thread(self, thread_id: str) -> bool:
        return await asyncio.to_thread(
            self.thread_repository.archive_thread, thread_id
        )

    async def update_thread(
        self,
        thread_id: str,
        *,
        title: str | None | UnsetType = UNSET,
        project_id: str | None | UnsetType = UNSET,
        is_archived: bool | UnsetType = UNSET,
        channel_id: str | UnsetType = UNSET,
        last_active_at: int | UnsetType = UNSET,
    ) -> ThreadInfo | None:
        return await asyncio.to_thread(
            self.thread_repository.update_thread,
            thread_id,
            title=title,
            project_id=project_id,
            is_archived=is_archived,
            channel_id=channel_id,
            last_active_at=last_active_at,
        )

    async def get_thread_history(self, thread_id: str) -> list[dict[str, Any]] | None:
        return await asyncio.to_thread(
            self.thread_repository.get_thread_history,
            thread_id,
        )

    async def sync_last_active_at(self, thread_id: str) -> int | None:
        return await asyncio.to_thread(
            self.thread_repository.sync_last_active_at,
            thread_id,
        )

    def get_background_thread(self, now_ts: float | None = None) -> Any:
        if self._thread_db_path is None:
            return None
        ts = now_ts if now_ts is not None else time.time()
        thread_id = f"background_{int(ts)}"
        self._persist_thread(thread_id, channel_id="unknown", now_ts=int(ts))

        return UnicodeSQLiteSession(
            thread_id, self._thread_db_path, sessions_table="sessions"
        )

    def _persist_thread(
        self,
        thread_id: str,
        *,
        channel_id: str,
        now_ts: int | None = None,
    ) -> None:
        if self._repository is None:
            return
        effective_now = now_ts if now_ts is not None else int(time.time())
        self._repository.create_thread(
            thread_id=thread_id,
            channel_id=channel_id,
            project_id=None,
            title=None,
            now_ts=effective_now,
        )

