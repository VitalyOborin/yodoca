"""Task Engine queries: get status, list active, cancel."""

import json
import logging
import time
from typing import Any

from sandbox.extensions.task_engine.chains import cancel_chain_downstream
from sandbox.extensions.task_engine.models import (
    ActiveTasksResult,
    CancelTaskResult,
    TaskStatusResult,
)
from sandbox.extensions.task_engine.state import TaskState

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = (
    "pending",
    "blocked",
    "running",
    "retry_scheduled",
    "waiting_subtasks",
    "human_review",
)
ALL_TASK_STATUSES = ACTIVE_TASK_STATUSES + ("done", "failed", "cancelled")


def _to_task_status_result(cols: list[str], row: Any) -> TaskStatusResult:
    d = dict(zip(cols, row, strict=False))
    payload = json.loads(d["payload"]) if isinstance(d["payload"], str) else {}
    checkpoint = None
    step_val = 0
    if d.get("checkpoint"):
        try:
            state = TaskState.from_json(d["checkpoint"])
            checkpoint = state.partial_result
            step_val = state.step
        except Exception:
            pass
    return TaskStatusResult(
        task_id=d["task_id"],
        status=d["status"],
        agent_id=d["agent_id"],
        goal=payload.get("goal", ""),
        step=step_val,
        max_steps=payload.get("max_steps", 20),
        attempt_no=d.get("attempt_no") or 0,
        partial_result=checkpoint,
        error=d.get("error"),
        created_at=int(d.get("created_at") or 0),
        updated_at=int(d.get("updated_at") or 0),
        chain_id=d.get("chain_id"),
        chain_order=d.get("chain_order"),
    )


async def get_task_status(db: Any, task_id: str) -> TaskStatusResult:
    """Get current status, progress, and partial result of a task."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        "SELECT * FROM agent_task WHERE task_id = ?", (task_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return TaskStatusResult(
            task_id=task_id,
            status="not_found",
            agent_id="",
            goal="",
            step=0,
            max_steps=0,
            attempt_no=0,
            error="Task not found",
        )
    cols = [d[0] for d in cursor.description]
    return _to_task_status_result(cols, row)


async def list_active_tasks(db: Any) -> ActiveTasksResult:
    """List all running and pending tasks with statuses and progress."""
    conn = await db.ensure_conn()
    placeholders = ",".join("?" for _ in ACTIVE_TASK_STATUSES)
    cursor = await conn.execute(
        f"""
        SELECT * FROM agent_task
        WHERE status IN ({placeholders})
        ORDER BY priority DESC, created_at ASC
        """,
        ACTIVE_TASK_STATUSES,
    )
    rows = await cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    tasks = [_to_task_status_result(cols, row) for row in rows]
    return ActiveTasksResult(tasks=tasks, total=len(tasks))


async def list_tasks(db: Any, status: str = "active") -> ActiveTasksResult:
    """List tasks by status filter.

    status:
      - active: non-terminal statuses
      - all: all statuses
      - specific status from ALL_TASK_STATUSES
    """
    conn = await db.ensure_conn()
    if status == "active":
        status_values = ACTIVE_TASK_STATUSES
    elif status == "all":
        status_values = ALL_TASK_STATUSES
    elif status in ALL_TASK_STATUSES:
        status_values = (status,)
    else:
        raise ValueError(f"Invalid task status filter: {status}")

    placeholders = ",".join("?" for _ in status_values)
    cursor = await conn.execute(
        f"""
        SELECT * FROM agent_task
        WHERE status IN ({placeholders})
        ORDER BY priority DESC, created_at ASC
        """,
        status_values,
    )
    rows = await cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    tasks = [_to_task_status_result(cols, row) for row in rows]
    return ActiveTasksResult(tasks=tasks, total=len(tasks))


async def cancel_task(db: Any, task_id: str, reason: str = "") -> CancelTaskResult:
    """Cancel a task. Works on pending, blocked, running, waiting, and human_review tasks.
    Also cascades cancellation to downstream blocked tasks (ADR 018)."""
    conn = await db.ensure_conn()
    cancellable = (
        "pending",
        "blocked",
        "retry_scheduled",
        "running",
        "waiting_subtasks",
        "human_review",
    )
    placeholders = ",".join("?" for _ in cancellable)
    cursor = await conn.execute(
        f"UPDATE agent_task SET status = 'cancelled', error = ?, updated_at = ? WHERE task_id = ? AND status IN ({placeholders})",
        (reason or "Cancelled by user", int(time.time()), task_id, *cancellable),
    )
    await conn.commit()
    if cursor.rowcount:
        await cancel_chain_downstream(db, task_id, reason or "Cancelled by user")
        return CancelTaskResult(
            task_id=task_id, status="cancelled", message="Task cancelled"
        )
    return CancelTaskResult(
        task_id=task_id,
        status="not_found",
        message="Task not found or already completed",
    )
