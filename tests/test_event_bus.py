"""Tests for EventBus: publish, subscribe, recover, retry/dead-letter."""

import asyncio
from pathlib import Path
import pytest

from core.events import EventBus
from core.events.models import Event


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "event_journal.db"


@pytest.fixture
async def event_bus(db_path: Path) -> EventBus:
    bus = EventBus(db_path=db_path, poll_interval=0.1, batch_size=5, max_retries=2)
    await bus.recover()
    yield bus
    await bus.stop()


class TestEventBusPublishSubscribe:
    """Publish and subscribe basics."""

    @pytest.mark.asyncio
    async def test_publish_returns_event_id(self, event_bus: EventBus) -> None:
        event_id = await event_bus.publish("test.topic", "source", {"key": "value"})
        assert isinstance(event_id, int)
        assert event_id > 0

    @pytest.mark.asyncio
    async def test_subscribe_and_deliver(self, event_bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        event_bus.subscribe("test.topic", handler, "test_sub")
        await event_bus.start()

        await event_bus.publish("test.topic", "src", {"data": 1})
        await asyncio.sleep(0.3)

        assert len(received) == 1
        assert received[0].topic == "test.topic"
        assert received[0].payload == {"data": 1}

    @pytest.mark.asyncio
    async def test_no_handler_marks_done(self, event_bus: EventBus) -> None:
        await event_bus.start()
        await event_bus.publish("orphan.topic", "src", {})
        await asyncio.sleep(0.3)
        # Should not raise; event marked done
        await event_bus.stop()


class TestEventBusRecover:
    """Recovery of processing events."""

    @pytest.mark.asyncio
    async def test_recover_resets_processing_to_pending(
        self, db_path: Path
    ) -> None:
        import time

        bus = EventBus(db_path=db_path, poll_interval=0.1, batch_size=5)
        await bus.recover()

        journal = bus._journal
        conn = await journal._ensure_conn()
        now = time.time()
        await conn.execute(
            """
            INSERT INTO event_journal (topic, source, payload, status, created_at)
            VALUES ('x', 'y', '{}', 'processing', ?)
            """,
            (now,),
        )
        await conn.commit()

        count = await bus.recover()
        assert count == 1

        cursor = await conn.execute(
            "SELECT status FROM event_journal WHERE status = 'pending'"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        await bus.stop()


class TestEventBusRetryAndDeadLetter:
    """Retry and dead-letter on handler failure."""

    @pytest.mark.asyncio
    async def test_handler_failure_retries_then_dead_letters(
        self, event_bus: EventBus
    ) -> None:
        call_count = 0

        async def failing_handler(event: Event) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("handler failed")

        event_bus.subscribe("fail.topic", failing_handler, "fail_sub")
        await event_bus.start()

        await event_bus.publish("fail.topic", "src", {})
        await asyncio.sleep(1.0)

        # max_retries=2: initial + 2 retries = 3 attempts, then dead-letter
        assert call_count >= 2

        conn = await event_bus._journal._ensure_conn()
        cursor = await conn.execute(
            "SELECT status, retry_count FROM event_journal"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        status, retry_count = rows[0]
        assert status == "dead_letter"
        assert retry_count >= 2
