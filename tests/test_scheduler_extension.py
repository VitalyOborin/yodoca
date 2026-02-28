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
            topic="test.topic", payload='{"x":1}', fire_at=time.time() + 60
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
    async def test_cancel_one_shot_returns_bool(self, store: _SchedulerStore) -> None:
        row_id = await store.insert_one_shot("t", "{}", time.time() + 60)
        ok = await store.cancel_one_shot(row_id)
        assert ok is True
        assert await store.cancel_one_shot(row_id) is False

    @pytest.mark.asyncio
    async def test_fetch_due_one_shot_and_mark_fired(
        self, store: _SchedulerStore
    ) -> None:
        now = time.time()
        row_id = await store.insert_one_shot("due.topic", "{}", now - 1)
        due = await store.fetch_due_one_shot(now)
        assert len(due) == 1
        assert due[0]["topic"] == "due.topic"
        await store.mark_one_shot_fired(row_id)
        rows = await store.list_all()
        assert rows[0]["status"] == "fired"

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
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        tools = ext.get_tools()
        assert len(tools) == 5
        names = [getattr(t, "name", str(t)) for t in tools]
        assert "schedule_once" in names
        assert "schedule_recurring" in names
        assert "list_schedules" in names
        assert "cancel_schedule" in names
        assert "update_recurring_schedule" in names
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_schedule_once_integration(self, tmp_path: Path) -> None:
        """Verify schedule_once flow: store.insert_one_shot only (no EventBus)."""
        data_dir = tmp_path / "scheduler"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = SchedulerExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.get_config = lambda k, d=None: 30 if k == "tick_interval" else d
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        fire_at = time.time() + 10
        row_id = await ext._store.insert_one_shot(
            "reminder.test", '{"msg":"hello"}', fire_at
        )
        assert row_id > 0
        rows = await ext._store.list_all()
        assert any(
            r["topic"] == "reminder.test" and r["type"] == "one_shot" for r in rows
        )
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_one_shot_fires_via_tick_loop(self, tmp_path: Path) -> None:
        """One-shot due in past fires when tick loop runs; ctx.emit called."""
        data_dir = tmp_path / "scheduler"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = SchedulerExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.get_config = lambda k, d=None: 0.05 if k == "tick_interval" else d
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        await ext._store.insert_one_shot("tick.topic", '{"x":1}', time.time() - 1)
        task = asyncio.create_task(ext.run_background())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        ctx.emit.assert_called()
        call_args = ctx.emit.call_args
        assert call_args[0][0] == "tick.topic"
        assert call_args[0][1] == {"x": 1}
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_one_shot_status_fired_after_emit(self, tmp_path: Path) -> None:
        """One-shot status becomes 'fired' after emit and mark_one_shot_fired."""
        data_dir = tmp_path / "scheduler"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = SchedulerExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.get_config = lambda k, d=None: 30 if k == "tick_interval" else d
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        row_id = await ext._store.insert_one_shot("s.topic", "{}", time.time() - 1)
        due = await ext._store.fetch_due_one_shot(time.time())
        assert len(due) == 1
        payload = (
            json.loads(due[0]["payload"])
            if isinstance(due[0]["payload"], str)
            else due[0]["payload"]
        )
        await ctx.emit(due[0]["topic"], payload)
        await ext._store.mark_one_shot_fired(row_id)
        rows = await ext._store.list_all()
        assert any(r["id"] == row_id and r["status"] == "fired" for r in rows)
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_one_shot_cancel_before_fire(self, tmp_path: Path) -> None:
        """Cancel one-shot before fire; emit never called."""
        data_dir = tmp_path / "scheduler"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = SchedulerExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.get_config = lambda k, d=None: 0.05 if k == "tick_interval" else d
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        row_id = await ext._store.insert_one_shot(
            "cancel.topic", "{}", time.time() + 60
        )
        ok = await ext._store.cancel_one_shot(row_id)
        assert ok is True
        due = await ext._store.fetch_due_one_shot(time.time() + 120)
        assert len(due) == 0
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_recurring_fires_on_interval(self, tmp_path: Path) -> None:
        """Recurring with every_sec fires on each tick when due."""
        data_dir = tmp_path / "scheduler"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = SchedulerExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.get_config = lambda k, d=None: 0.05 if k == "tick_interval" else d
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        now = time.time()
        await ext._store.insert_recurring(
            "interval.topic", '{"n":1}', None, 0.1, None, now - 0.01
        )
        task = asyncio.create_task(ext.run_background())
        await asyncio.sleep(0.25)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert ctx.emit.call_count >= 2
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_recurring_cron_expression(self, store: _SchedulerStore) -> None:
        """Recurring with cron expression computes next_fire_at correctly."""
        now = time.time()
        row_id = await store.insert_recurring(
            "cron.topic", "{}", "0 * * * *", None, None, now + 3600
        )
        rows = await store.list_all()
        assert any(r["id"] == row_id and r["cron_expr"] == "0 * * * *" for r in rows)
        due = await store.fetch_due_recurring(now + 3700)
        assert len(due) == 1
        assert due[0]["topic"] == "cron.topic"

    @pytest.mark.asyncio
    async def test_recurring_until_expires(self, store: _SchedulerStore) -> None:
        """Recurring with until_at in past gets cancelled on advance."""
        now = time.time()
        past = now - 100
        row_id = await store.insert_recurring(
            "until.topic", "{}", None, 10.0, past, now - 1
        )
        await store.advance_next(row_id, now)
        rows = await store.list_all()
        assert any(r["id"] == row_id and r["status"] == "cancelled" for r in rows)

    @pytest.mark.asyncio
    async def test_recurring_pause_resume(self, store: _SchedulerStore) -> None:
        """Recurring can be paused and resumed via update_recurring."""
        now = time.time()
        row_id = await store.insert_recurring(
            "pause.topic", "{}", None, 60.0, None, now + 60
        )
        await store.update_recurring(row_id, status="paused")
        rows = await store.list_all()
        assert any(r["id"] == row_id and r["status"] == "paused" for r in rows)
        due = await store.fetch_due_recurring(now + 120)
        assert len(due) == 0
        await store.update_recurring(row_id, status="active")
        rows = await store.list_all()
        assert any(r["id"] == row_id and r["status"] == "active" for r in rows)

    @pytest.mark.asyncio
    async def test_recovery_overdue_one_shot_fires_on_start(
        self, tmp_path: Path
    ) -> None:
        """start() fires overdue one-shots immediately."""
        data_dir = tmp_path / "scheduler"
        data_dir.mkdir(parents=True, exist_ok=True)
        ext = SchedulerExtension()
        ctx = MagicMock()
        ctx.data_dir = data_dir
        ctx.get_config = lambda k, d=None: 30 if k == "tick_interval" else d
        ctx.emit = AsyncMock()
        await ext.initialize(ctx)
        await ext._store.insert_one_shot("recovery.topic", '{"r":1}', time.time() - 10)
        await ext.start()
        ctx.emit.assert_called_once()
        assert ctx.emit.call_args[0][0] == "recovery.topic"
        await ext.destroy()

    @pytest.mark.asyncio
    async def test_recovery_recurring_skip_missed(self, store: _SchedulerStore) -> None:
        """recover_recurring advances next_fire_at for missed recurring; does not fire missed."""
        now = time.time()
        row_id = await store.insert_recurring(
            "skip.topic", "{}", "0 * * * *", None, None, now - 3600
        )
        await store.recover_recurring(now)
        rows = await store.list_all()
        r = next(x for x in rows if x["id"] == row_id)
        assert r["fire_at_or_next"] > now
        due = await store.fetch_due_recurring(now)
        assert len(due) == 0
