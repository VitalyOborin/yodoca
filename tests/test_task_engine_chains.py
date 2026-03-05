"""Tests for task_engine chain logic (ADR 018)."""

import json
import sys
import tempfile
from pathlib import Path

import pytest

_task_engine_dir = (
    Path(__file__).resolve().parent.parent
    / "sandbox"
    / "extensions"
    / "task_engine"
)
if str(_task_engine_dir) not in sys.path:
    sys.path.insert(0, str(_task_engine_dir))

from schema import TaskEngineDb  # type: ignore[import-not-found]
from state import json_dumps_unicode  # type: ignore[import-not-found]


def _payload(goal: str, **kwargs: object) -> str:
    return json_dumps_unicode({"goal": goal, "max_steps": 5, **kwargs})


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
async def test_unblock_successors_on_done(temp_db: TaskEngineDb) -> None:
    """When predecessor completes with done, successor gets result and becomes pending."""
    from chains import unblock_successors  # type: ignore[import-not-found]

    conn = await temp_db.ensure_conn()
    now = 1000.0
    # Create predecessor (done) and successor (blocked)
    await conn.execute(
        """
        INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at, after_task_id)
        VALUES ('task-a', NULL, 'run-a', 'orchestrator', 'done', 5, ?, ?, ?, NULL)
        """,
        (_payload("Complete A"), now, now),
    )
    await conn.execute(
        """
        INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at, after_task_id)
        VALUES ('task-b', NULL, 'run-b', 'orchestrator', 'blocked', 5, ?, ?, ?, 'task-a')
        """,
        (_payload("Use result of A"), now, now),
    )
    await conn.commit()

    await unblock_successors(temp_db, "task-a", "done", {"content": "Result from A"})

    cursor = await conn.execute(
        "SELECT status, payload FROM agent_task WHERE task_id = 'task-b'"
    )
    row = await cursor.fetchone()
    assert row is not None
    status, payload_raw = row
    assert status == "pending"
    payload = json.loads(payload_raw)
    assert payload.get("predecessor_result") == "Result from A"
    assert payload.get("predecessor_task_id") == "task-a"


@pytest.mark.asyncio
async def test_unblock_successors_cascade_failure(temp_db: TaskEngineDb) -> None:
    """When predecessor fails, blocked successors are cascaded to failed."""
    from chains import unblock_successors  # type: ignore[import-not-found]

    conn = await temp_db.ensure_conn()
    now = 1000.0
    await conn.execute(
        """
        INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at, after_task_id)
        VALUES ('task-a', NULL, 'run-a', 'orchestrator', 'failed', 5, ?, ?, ?, NULL)
        """,
        (_payload("Fail A"), now, now),
    )
    await conn.execute(
        """
        INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at, after_task_id)
        VALUES ('task-b', NULL, 'run-b', 'orchestrator', 'blocked', 5, ?, ?, ?, 'task-a')
        """,
        (_payload("Depends on A"), now, now),
    )
    await conn.execute(
        """
        INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at, after_task_id)
        VALUES ('task-c', NULL, 'run-c', 'orchestrator', 'blocked', 5, ?, ?, ?, 'task-b')
        """,
        (_payload("Depends on B"), now, now),
    )
    await conn.commit()

    await unblock_successors(temp_db, "task-a", "failed", None)

    cursor = await conn.execute(
        "SELECT task_id, status, error FROM agent_task WHERE task_id IN ('task-b', 'task-c') ORDER BY task_id"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 2
    assert rows[0] == ("task-b", "failed", "Predecessor task-a failed")
    assert rows[1] == ("task-c", "failed", "Predecessor task-b failed")


@pytest.mark.asyncio
async def test_cancel_chain_downstream(temp_db: TaskEngineDb) -> None:
    """Cancel propagates to all downstream blocked tasks."""
    from chains import cancel_chain_downstream  # type: ignore[import-not-found]

    conn = await temp_db.ensure_conn()
    now = 1000.0
    await conn.execute(
        """
        INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at, after_task_id)
        VALUES ('task-a', NULL, 'run-a', 'orchestrator', 'cancelled', 5, ?, ?, ?, NULL)
        """,
        (_payload("Cancelled"), now, now),
    )
    await conn.execute(
        """
        INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at, after_task_id)
        VALUES ('task-b', NULL, 'run-b', 'orchestrator', 'blocked', 5, ?, ?, ?, 'task-a')
        """,
        (_payload("Blocked by A"), now, now),
    )
    await conn.execute(
        """
        INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at, after_task_id)
        VALUES ('task-c', NULL, 'run-c', 'orchestrator', 'blocked', 5, ?, ?, ?, 'task-b')
        """,
        (_payload("Blocked by B"), now, now),
    )
    await conn.commit()

    n = await cancel_chain_downstream(temp_db, "task-a", "User cancelled")

    assert n == 2
    cursor = await conn.execute(
        "SELECT task_id, status FROM agent_task WHERE task_id IN ('task-b', 'task-c') ORDER BY task_id"
    )
    rows = await cursor.fetchall()
    assert rows == [("task-b", "cancelled"), ("task-c", "cancelled")]


@pytest.mark.asyncio
async def test_get_chain_tasks(temp_db: TaskEngineDb) -> None:
    """get_chain_tasks returns tasks ordered by chain_order."""
    from chains import get_chain_tasks  # type: ignore[import-not-found]

    conn = await temp_db.ensure_conn()
    now = 1000.0
    for i, (tid, goal) in enumerate([("t1", "First"), ("t2", "Second"), ("t3", "Third")]):
        await conn.execute(
            """
            INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at, chain_id, chain_order)
            VALUES (?, NULL, ?, 'orchestrator', 'pending', 5, ?, ?, ?, 'chain-1', ?)
            """,
            (tid, f"run-{tid}", _payload(goal), now, now, i),
        )
    await conn.commit()

    tasks = await get_chain_tasks(temp_db, "chain-1")
    assert len(tasks) == 3
    assert tasks[0]["task_id"] == "t1" and tasks[0]["goal"] == "First"
    assert tasks[1]["task_id"] == "t2" and tasks[1]["goal"] == "Second"
    assert tasks[2]["task_id"] == "t3" and tasks[2]["goal"] == "Third"


@pytest.mark.asyncio
async def test_get_chain_tasks_empty(temp_db: TaskEngineDb) -> None:
    """get_chain_tasks returns empty list for unknown chain."""
    from chains import get_chain_tasks  # type: ignore[import-not-found]

    tasks = await get_chain_tasks(temp_db, "nonexistent")
    assert tasks == []


@pytest.mark.asyncio
async def test_predecessor_result_in_prompt() -> None:
    """predecessor_result appears in _build_step_prompt when provided."""
    from state import TaskState  # type: ignore[import-not-found]
    from worker import _build_step_prompt  # type: ignore[import-not-found]

    state = TaskState(goal="Continue from previous")
    prompt = _build_step_prompt(
        state=state,
        max_steps=5,
        predecessor_result="Previous step produced: summary of research",
    )
    assert "Previous step result" in prompt
    assert "summary of research" in prompt
