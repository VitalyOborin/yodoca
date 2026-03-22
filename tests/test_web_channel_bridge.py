"""Tests for web_channel RequestBridge."""

import pytest

from sandbox.extensions.web_channel.bridge import STREAM_END, RequestBridge


class TestRequestBridge:
    """RequestBridge: busy guard, future, stream queue, notifications."""

    @pytest.mark.asyncio
    async def test_acquire_release(self) -> None:
        bridge = RequestBridge(request_timeout_seconds=5.0)
        got = await bridge.acquire()
        assert got is True
        bridge.release()

    @pytest.mark.asyncio
    async def test_acquire_busy_returns_false(self) -> None:
        bridge = RequestBridge(request_timeout_seconds=5.0)
        got1 = await bridge.acquire()
        assert got1 is True
        got2 = await bridge.acquire()
        assert got2 is False
        bridge.release()

    @pytest.mark.asyncio
    async def test_resolve_response_sets_future(self) -> None:
        bridge = RequestBridge(request_timeout_seconds=5.0)
        await bridge.acquire()
        future = bridge.create_future()
        bridge.resolve_response("hello")
        result = await future
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_push_stream_event_and_end(self) -> None:
        bridge = RequestBridge(request_timeout_seconds=5.0)
        await bridge.acquire()
        queue = bridge.create_stream_queue()
        bridge.push_stream_event("start", None)
        bridge.push_stream_event("chunk", "hi")
        bridge.push_stream_event("end", "hi")
        bridge.push_stream_end()

        items = []
        while True:
            item = await queue.get()
            if item is STREAM_END:
                break
            items.append(item)
        assert items == [("start", None), ("chunk", "hi"), ("end", "hi")]

    @pytest.mark.asyncio
    async def test_push_stream_end_resolves_non_stream_future(self) -> None:
        bridge = RequestBridge(request_timeout_seconds=5.0)
        await bridge.acquire()
        future = bridge.create_future()
        bridge.push_stream_end("final text")
        result = await future
        assert result == "final text"

    @pytest.mark.asyncio
    async def test_push_notification_and_wait(self) -> None:
        bridge = RequestBridge(request_timeout_seconds=5.0)
        bridge.push_notification("msg1")
        notifications = await bridge.wait_notification(timeout=0.1)
        assert len(notifications) == 1
        assert notifications[0]["text"] == "msg1"
        assert "id" in notifications[0]
        assert "created_at" in notifications[0]

    @pytest.mark.asyncio
    async def test_wait_notification_empty_returns_after_timeout(self) -> None:
        bridge = RequestBridge(request_timeout_seconds=5.0)
        notifications = await bridge.wait_notification(timeout=0.05)
        assert notifications == []
