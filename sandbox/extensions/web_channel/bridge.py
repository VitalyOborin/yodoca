"""Request-response bridging: busy guard, future/queue, notifications."""

import asyncio
import collections
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel for stream end
STREAM_END = object()


class RequestBridge:
    """Bridges HTTP request-response with ChannelProvider callbacks.

    - Busy guard: only one request at a time; 503 when held
    - Non-streaming: asyncio.Future for send_to_user
    - Streaming: asyncio.Queue for on_stream_* callbacks
    - Notifications: ring buffer + long-poll
    """

    def __init__(self, request_timeout_seconds: float = 120.0) -> None:
        self._request_timeout = request_timeout_seconds
        self._busy = asyncio.Lock()
        self._held = False
        self._active_future: asyncio.Future[str] | None = None
        self._active_stream: asyncio.Queue[Any] | None = None
        self._notifications: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=100
        )
        self._notify_event = asyncio.Event()
        self._timeout_task: asyncio.Task[Any] | None = None

    async def acquire(self) -> bool:
        """Try to acquire the busy guard. Returns False if already held."""
        if self._busy.locked():
            return False
        try:
            await asyncio.wait_for(self._busy.acquire(), timeout=0.1)
            self._held = True
            return True
        except TimeoutError:
            return False

    def release(self) -> None:
        """Release the busy guard (called from send_to_user or on_stream_end)."""
        if self._held:
            self._held = False
            try:
                self._busy.release()
            except RuntimeError:
                pass
        self._cancel_timeout()

    def _cancel_timeout(self) -> None:
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
            self._timeout_task = None

    def _start_timeout(self) -> None:
        """Start a safety timeout that releases the guard if callback never arrives."""

        async def _timeout() -> None:
            try:
                await asyncio.sleep(self._request_timeout)
                logger.warning(
                    "RequestBridge: safety timeout reached, releasing busy guard"
                )
                self.resolve_response("(Request timeout)")
            except asyncio.CancelledError:
                pass

        self._timeout_task = asyncio.create_task(_timeout())

    def resolve_response(self, text: str) -> None:
        """Set the active future result (call from send_to_user)."""
        if self._active_future and not self._active_future.done():
            self._active_future.set_result(text)
        self.release()

    def push_stream_event(self, event_type: str, data: Any) -> None:
        """Put an event into the active stream queue."""
        if self._active_stream is not None:
            self._active_stream.put_nowait((event_type, data))

    def push_stream_end(self, final_text: str | None = None) -> None:
        """Signal stream end and resolve any waiting non-stream future.

        The router may invoke streaming callbacks for channels that support
        streaming even when the originating HTTP endpoint expects a single JSON
        response. In that case there is no active stream queue, but there may be
        an active future created by the non-stream endpoint. Resolving it here
        prevents false request timeouts.
        """
        if (
            final_text is not None
            and self._active_future is not None
            and not self._active_future.done()
        ):
            self._active_future.set_result(final_text)
        if self._active_stream is not None:
            self._active_stream.put_nowait(STREAM_END)
        self.release()

    def push_notification(self, text: str) -> None:
        """Append to notification deque and set notify event."""
        self._notifications.append(
            {
                "id": f"notif_{uuid.uuid4().hex[:16]}",
                "text": text,
                "created_at": int(time.time()),
            }
        )
        self._notify_event.set()

    async def wait_notification(self, timeout: float = 30.0) -> list[dict[str, Any]]:
        """Wait for notifications (long-poll). Returns consumed notifications."""
        result: list[dict[str, Any]] = []
        while self._notifications:
            result.append(self._notifications.popleft())
        if result:
            if not self._notifications:
                self._notify_event.clear()
            return result
        self._notify_event.clear()
        try:
            await asyncio.wait_for(self._notify_event.wait(), timeout=timeout)
        except TimeoutError:
            return []
        while self._notifications:
            result.append(self._notifications.popleft())
        if not self._notifications:
            self._notify_event.clear()
        return result

    def create_future(self) -> asyncio.Future[str]:
        """Create and store the active future for non-streaming."""
        self._active_future = asyncio.get_event_loop().create_future()
        self._start_timeout()
        return self._active_future

    def create_stream_queue(self) -> asyncio.Queue[Any]:
        """Create and store the active stream queue for streaming."""
        self._active_stream = asyncio.Queue()
        self._start_timeout()
        return self._active_stream

    def clear_active(self) -> None:
        """Clear active future/stream after request completes."""
        self._active_future = None
        self._active_stream = None
