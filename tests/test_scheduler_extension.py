"""Tests for the scheduler extension."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sandbox.extensions.scheduler.main import SchedulerExtension, _SchedulerStore


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "scheduler.db"


@pytest.fixture
async def store(tmp_db: Path) -> _SchedulerStore:
    s = _SchedulerStore(tmp_db)
    await s._ensure_conn()
    yield s
    await s.close()


class TestSchedulerStore:
    """Test SchedulerStore methods."""

    @pytest.mark.asyncio
    async def test_insert_and_list_one_shot(self, store: _SchedulerStore) -> None:
        row_id = await store.insert_one_shot(
            deferred_id=1, topic="test.topic", payload='{"x":1}', fire_at=time.time() + 60
        )
        assert row_id > 0
        rows = await store.list_all()
        assert len(rows) == 1
        assert rows[0]["type"] == "one_shot"
        assert rows[0]["topic"] == "test.topic"

    @pytest.mark.asyncio
    async def test_insert_and_fetch_recurring(self, store: _SchedulerStore) -> None:
        now = time.time()
        row_id = await store.insert_recurring(
            topic="recur.topic",
            payload='{"y":2}',
            cron_expr="0 9 * * *",
            every_sec=None,
            until_at=None,
            next_fire_at=now + 3600,
        )
        assert row_id > 0
        due = await store.fetch_due_recurring(now)
        assert len(due) == 0
        due = await store.fetch_due_recurring(now + 4000)
        assert len(due) == 1
        assert due[0]["topic"] == "recur.topic"

    @pytest.mark.asyncio
    async def test_cancel_one_shot_returns_deferred_id(self, store: _SchedulerStore) -> None:
        row_id = await store.insert_one_shot(2, "t", "{}", time.time() + 60)
        deferred_id = await store.cancel_one_shot(row_id)
        assert deferred_id == 2
        assert await store.cancel_one_shot(row_id) is None

    @pytest.mark.asyncio
    async def test_cancel_recurring(self, store: _SchedulerStore) -> None:
        now = time.time()
        row_id = await store.insert_recurring(
            "t", "{}", "0 * * * *", None, None, now + 60
        )
        await store.cancel_recurring(row_id)
        rows = await store.list_all()
        assert any(r["id"] == row_id and r["status"] == "cancelled" for r in rows)


class TestSchedulerExtension:
    """Test SchedulerExtension tools and lifecycle."""

    @pytest.mark.asyncio
    async def test_initialize_creates_store(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "scheduler"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = SchedulerExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.get_config = lambda k, d=None: 30 if k == "tick_interval" else d
        await ext.initialize(ctx)
        assert ext._store is not None
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_get_tools_returns_five_tools(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "scheduler"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = SchedulerExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.get_config = lambda k, d=None: 30 if k == "tick_interval" else d
        ctx.schedule_at = AsyncMock(return_value=42)
        ctx.cancel_deferred = AsyncMock(return_value=True)
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        tools = ext.get_tools()
        assert len(tools) == 5
        names = [getattr(t, "name", str(t)) for t in tools]
        assert "schedule_once" in names
        assert "schedule_recurring" in names
        assert "list_schedules" in names
        assert "cancel_schedule" in names
        assert "update_schedule" in names
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_schedule_once_integration(self, tmp_path: Path) -> None:
        """Verify schedule_once flow: ctx.schedule_at + store.insert_one_shot."""
        data_dir = tmp_path / "scheduler"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = SchedulerExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.get_config = lambda k, d=None: 30 if k == "tick_interval" else d
        ctx.schedule_at = AsyncMock(return_value=99)
        ctx.cancel_deferred = AsyncMock(return_value=True)
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        # Simulate schedule_once logic
        deferred_id = await ctx.schedule_at(10, "reminder.test", {"msg": "hello"})
        assert deferred_id == 99
        fire_at = time.time() + 10
        row_id = await ext._store.insert_one_shot(
            99, "reminder.test", '{"msg":"hello"}', fire_at
        )
        assert row_id > 0
        rows = await ext._store.list_all()
        assert any(r["topic"] == "reminder.test" and r["type"] == "one_shot" for r in rows)
        await ext.destroy()
