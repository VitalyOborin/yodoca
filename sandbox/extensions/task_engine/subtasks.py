"""Task Engine subtask logic: depth, checkpoint update, resume, collect results."""

import json
import logging
import time
from typing import Any

from state import TaskState

logger = logging.getLogger(__name__)

MAX_SUBTASK_DEPTH = 3


async def get_subtask_depth(db: Any, task_id: str) -> int:
    """Count ancestor depth by walking parent_id chain."""
    conn = await db.ensure_conn()
    depth = 0
    current = task_id
    while current and depth <= MAX_SUBTASK_DEPTH:
        cursor = await conn.execute(
            "SELECT parent_id FROM agent_task WHERE task_id = ?", (current,)
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            break
        current = row[0]
        depth += 1
    return depth


async def update_parent_checkpoint(db: Any, parent_task_id: str, child_task_id: str) -> None:
    """Append child task_id to parent's pending_subtasks in checkpoint."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        "SELECT checkpoint, payload FROM agent_task WHERE task_id = ?", (parent_task_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return
    checkpoint_raw, payload_raw = row[0], row[1]
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
    goal = payload.get("goal", "")
    try:
        state = TaskState.from_json(checkpoint_raw) if checkpoint_raw else TaskState(goal=goal)
    except Exception:
        state = TaskState(goal=goal)
    if child_task_id not in state.pending_subtasks:
        state.pending_subtasks = list(state.pending_subtasks) + [child_task_id]
    await conn.execute(
        "UPDATE agent_task SET checkpoint = ?, updated_at = ? WHERE task_id = ?",
        (state.to_json(), time.time(), parent_task_id),
    )


async def collect_subtask_results(db: Any, parent_id: str) -> tuple[list[dict], list[dict]]:
    """Query all children of parent, return (results, failures) tuples."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        "SELECT task_id, status, result, error FROM agent_task WHERE parent_id = ?",
        (parent_id,),
    )
    rows = await cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    results = []
    failures = []
    for row in rows:
        d = dict(zip(cols, row))
        task_id = d["task_id"]
        status = d["status"]
        result = None
        if d.get("result"):
            try:
                result = json.loads(d["result"]) if isinstance(d["result"], str) else d["result"]
            except Exception:
                result = d.get("result")
        if status == "done":
            results.append({"task_id": task_id, "status": status, "result": result})
        else:
            failures.append({"task_id": task_id, "status": status, "error": d.get("error") or "unknown"})
    return (results, failures)


async def try_resume_parent(db: Any, parent_id: str) -> None:
    """If all siblings are terminal, inject results into parent and set parent to pending."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        """
        SELECT COUNT(*) as cnt FROM agent_task
        WHERE parent_id = ? AND status NOT IN ('done', 'failed', 'cancelled')
        """,
        (parent_id,),
    )
    row = await cursor.fetchone()
    if not row or row[0] != 0:
        return

    results, failures = await collect_subtask_results(db, parent_id)
    cursor = await conn.execute(
        "SELECT checkpoint, payload FROM agent_task WHERE task_id = ? AND status = 'waiting_subtasks'",
        (parent_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return
    checkpoint_raw, payload_raw = row[0], row[1]
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
    goal = payload.get("goal", "")
    try:
        state = TaskState.from_json(checkpoint_raw) if checkpoint_raw else TaskState(goal=goal)
    except Exception:
        state = TaskState(goal=goal)
    state.context = dict(state.context)
    state.context["subtask_results"] = results
    state.context["subtask_failures"] = failures
    await conn.execute(
        "UPDATE agent_task SET status = 'pending', checkpoint = ?, updated_at = ? WHERE task_id = ?",
        (state.to_json(), time.time(), parent_id),
    )
    await conn.commit()
    logger.info("task_engine: resumed parent %s with %d results, %d failures", parent_id, len(results), len(failures))
