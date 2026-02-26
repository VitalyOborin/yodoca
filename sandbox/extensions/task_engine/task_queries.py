"""Task Engine queries: get status, list active, cancel."""

import json
import logging
import time
from typing import Any

from models import ActiveTasksResult, CancelTaskResult, TaskStatusResult
from state import TaskState

logger = logging.getLogger(__name__)


async def get_task_status(db: Any, task_id: str) -> TaskStatusResult:
    """Get current status, progress, and partial result of a task."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        "SELECT * FROM agent_task WHERE task_id = ?", (task_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return TaskStatusResult(
            task_id=task_id, status="not_found", agent_id="", goal="", step=0, max_steps=0,
            attempt_no=0, error="Task not found",
        )
    cols = [d[0] for d in cursor.description]
    d = dict(zip(cols, row))
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
        created_at=d.get("created_at") or 0,
        updated_at=d.get("updated_at") or 0,
    )


async def list_active_tasks(db: Any) -> ActiveTasksResult:
    """List all running and pending tasks with statuses and progress."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        """
        SELECT * FROM agent_task
        WHERE status IN ('pending', 'running', 'retry_scheduled', 'waiting_subtasks', 'human_review')
        ORDER BY priority DESC, created_at ASC
        """
    )
    rows = await cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    tasks = []
    for row in rows:
        d = dict(zip(cols, row))
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
        tasks.append(
            TaskStatusResult(
                task_id=d["task_id"],
                status=d["status"],
                agent_id=d["agent_id"],
                goal=payload.get("goal", ""),
                step=step_val,
                max_steps=payload.get("max_steps", 20),
                attempt_no=d.get("attempt_no") or 0,
                partial_result=checkpoint,
                error=d.get("error"),
                created_at=d.get("created_at") or 0,
                updated_at=d.get("updated_at") or 0,
            )
        )
    return ActiveTasksResult(tasks=tasks, total=len(tasks))


async def cancel_task(db: Any, task_id: str, reason: str = "") -> CancelTaskResult:
    """Cancel a task. Works on pending, running, waiting, and human_review tasks."""
    conn = await db.ensure_conn()
    cancellable = ("pending", "retry_scheduled", "running", "waiting_subtasks", "human_review")
    placeholders = ",".join("?" for _ in cancellable)
    cursor = await conn.execute(
        f"UPDATE agent_task SET status = 'cancelled', error = ?, updated_at = ? WHERE task_id = ? AND status IN ({placeholders})",
        (reason or "Cancelled by user", time.time(), task_id, *cancellable),
    )
    await conn.commit()
    if cursor.rowcount:
        return CancelTaskResult(task_id=task_id, status="cancelled", message="Task cancelled")
    return CancelTaskResult(task_id=task_id, status="not_found", message="Task not found or already completed")
