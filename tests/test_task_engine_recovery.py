"""Tests for task_engine stale-task recovery behavior after restart."""

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sandbox.extensions.task_engine.main import TaskEngineExtension
from sandbox.extensions.task_engine.schema import TaskEngineDb
from sandbox.extensions.task_engine.state import json_dumps_unicode
from sandbox.extensions.task_engine.worker import (
    RESTART_INTERRUPTED_ERROR,
    claim_next_task,
    recover_stale_tasks,
)


def _payload(goal: str) -> str:
    return json_dumps_unicode({"goal": goal, "max_steps": 5})


@pytest.fixture
async def temp_db():
    """Create a temporary task_engine database with schema."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "task_engine.db"
        db = TaskEngineDb(db_path)
        await db.ensure_conn()
        try:
            yield db
        finally:
            await db.close()


@pytest.mark.asyncio
async def test_recover_stale_tasks_fails_task_and_excludes_from_claim(
    temp_db: TaskEngineDb,
) -> None:
    """Expired running lease should fail task (not requeue), and not be claimable."""
    conn = await temp_db.ensure_conn()
    now = 1000.0
    await conn.execute(
        """
        INSERT INTO agent_task (
            task_id, parent_id, run_id, agent_id, status, priority, payload,
            leased_by, lease_exp, created_at, updated_at
        )
        VALUES ('stale-1', NULL, 'run-1', 'orchestrator', 'running', 5, ?, 'worker-a', ?, ?, ?)
        """,
        (_payload("stale goal"), now - 10, now, now),
    )
    await conn.commit()

    recovered = await recover_stale_tasks(temp_db)
    assert recovered == [{"task_id": "stale-1", "parent_id": None}]

    cursor = await conn.execute(
        "SELECT status, leased_by, lease_exp, error FROM agent_task WHERE task_id = 'stale-1'"
    )
    row = await cursor.fetchone()
    assert row == ("failed", None, None, RESTART_INTERRUPTED_ERROR)

    claimed = await claim_next_task(temp_db, worker_id="worker-b", lease_ttl=60.0)
    assert claimed is None


@pytest.mark.asyncio
async def test_run_background_emits_failed_task_completed_for_recovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_background emits task.completed=failed for recovered stale tasks on startup."""
    ext = TaskEngineExtension()
    emit = AsyncMock()
    ext._ctx = SimpleNamespace(default_agent_id="orchestrator", emit=emit)
    ext._db = object()
    ext._worker_id = "worker-x"
    ext._lease_ttl = 90.0
    ext._max_retries = 5

    async def fake_recover(_db: object) -> list[dict[str, str | None]]:
        return [{"task_id": "stale-1", "parent_id": None}]

    async def fake_claim(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "sandbox.extensions.task_engine.main.recover_stale_tasks", fake_recover
    )
    monkeypatch.setattr(
        "sandbox.extensions.task_engine.main.claim_next_task", fake_claim
    )

    with pytest.raises(asyncio.CancelledError):
        await ext.run_background()

    emit.assert_awaited_once()
    topic, payload = emit.await_args.args
    assert topic == "task.completed"
    assert payload["task_id"] == "stale-1"
    assert payload["status"] == "failed"
    assert payload["error"] == RESTART_INTERRUPTED_ERROR


@pytest.mark.asyncio
async def test_recovered_failed_event_cascades_to_blocked_successor(
    tmp_path: Path,
) -> None:
    """Recovered stale predecessor should cascade failure to blocked chain successor."""
    ext = TaskEngineExtension()
    data_dir = tmp_path / "task_engine"
    data_dir.mkdir(parents=True, exist_ok=True)
    ctx = MagicMock()
    ctx.data_dir = data_dir
    ctx.get_config = lambda k, d=None: d
    ctx.agent_registry = None
    ctx.subscribe_event = MagicMock()
    ctx.notify_user = AsyncMock()
    await ext.initialize(ctx)
    assert ext._db is not None

    conn = await ext._db.ensure_conn()
    now = 1000.0
    await conn.execute(
        """
        INSERT INTO agent_task (
            task_id, parent_id, run_id, agent_id, status, priority, payload,
            leased_by, lease_exp, created_at, updated_at
        )
        VALUES ('task-a', NULL, 'run-a', 'orchestrator', 'running', 5, ?, 'worker-a', ?, ?, ?)
        """,
        (_payload("predecessor"), now - 10, now, now),
    )
    await conn.execute(
        """
        INSERT INTO agent_task (
            task_id, parent_id, run_id, agent_id, status, priority, payload,
            after_task_id, created_at, updated_at
        )
        VALUES ('task-b', NULL, 'run-b', 'orchestrator', 'blocked', 5, ?, 'task-a', ?, ?)
        """,
        (_payload("successor"), now, now),
    )
    await conn.commit()

    recovered = await recover_stale_tasks(ext._db)
    assert recovered == [{"task_id": "task-a", "parent_id": None}]
    for task_ref in recovered:
        await ext._on_task_completed(
            SimpleNamespace(
                payload={
                    "task_id": task_ref["task_id"],
                    "parent_id": task_ref["parent_id"],
                    "status": "failed",
                    "error": RESTART_INTERRUPTED_ERROR,
                }
            )
        )

    cursor = await conn.execute(
        "SELECT status, error FROM agent_task WHERE task_id = 'task-b'"
    )
    row = await cursor.fetchone()
    assert row == ("failed", "Predecessor task-a failed")

    await ext.destroy()
