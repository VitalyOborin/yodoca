"""Tests for deferred event scheduler (schedule_at)."""

import asyncio
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.events import EventBus


@pytest.mark.asyncio
async def test_schedule_at_fires_after_delay(tmp_path: Path) -> None:
    """schedule_at(now+0.1, ...) fires after ~0.1s; handler called, journal done, deferred fired."""
    db_path = tmp_path / "events.db"
    event_bus = EventBus(db_path=db_path, poll_interval=0.1)

    handler = AsyncMock()
    event_bus.subscribe("test.topic", handler, "test_subscriber")

    await event_bus.recover()
    await event_bus.start()

    fire_at = time.time() + 0.15
    deferred_id = await event_bus.schedule_at(
        fire_at=fire_at,
        topic="test.topic",
        payload={"msg": "hello"},
        source="test",
    )
    assert deferred_id > 0

    for _ in range(20):
        await asyncio.sleep(0.1)
        if handler.called:
            break

    await event_bus.stop()

    assert handler.called, "Handler should have been called"
    call_args = handler.call_args[0][0]
    assert call_args.topic == "test.topic"
    assert call_args.payload == {"msg": "hello"}

    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT id, topic, status FROM event_journal WHERE topic = 'test.topic'"
    )
    rows = cur.fetchall()
    conn.close()
    assert len(rows) >= 1
    assert any(r[2] == "done" for r in rows)

    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT id, status FROM deferred_events WHERE id = ?", (deferred_id,)
    )
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[1] == "fired"


@pytest.mark.asyncio
async def test_schedule_at_recovery(tmp_path: Path) -> None:
    """Deferred events with fire_at in past are promoted to event_journal on recover()."""
    db_path = tmp_path / "events.db"
    event_bus = EventBus(db_path=db_path, poll_interval=0.1)

    await event_bus.recover()
    await event_bus.start()
    await asyncio.sleep(0.15)
    await event_bus.stop()

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO deferred_events (topic, source, payload, fire_at, status, created_at)
        VALUES ('recovery.topic', 'test', '{"x":1}', ?, 'scheduled', ?)
        """,
        (time.time() - 10, time.time()),
    )
    conn.commit()
    conn.close()

    event_bus2 = EventBus(db_path=db_path, poll_interval=0.1)
    count = await event_bus2.recover()

    assert count >= 1

    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT id, topic, status FROM event_journal WHERE topic = 'recovery.topic'"
    )
    rows = cur.fetchall()
    conn.close()
    assert len(rows) >= 1
    assert any(r[2] == "pending" for r in rows)


@pytest.mark.asyncio
async def test_schedule_at_does_not_fire_early(tmp_path: Path) -> None:
    """schedule_at(now+3600, ...) does not fire within 0.5s."""
    db_path = tmp_path / "events.db"
    event_bus = EventBus(db_path=db_path, poll_interval=0.1)

    handler = AsyncMock()
    event_bus.subscribe("future.topic", handler, "test_subscriber")

    await event_bus.recover()
    await event_bus.start()

    fire_at = time.time() + 3600
    await event_bus.schedule_at(
        fire_at=fire_at,
        topic="future.topic",
        payload={"msg": "far future"},
        source="test",
    )

    await asyncio.sleep(0.5)

    await event_bus.stop()

    assert not handler.called, "Handler should not have been called for future event"
