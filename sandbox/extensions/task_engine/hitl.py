"""Task Engine human-in-the-loop: pause for review, resume with response."""

import json
import logging
import time
from typing import Any

from models import SubmitTaskResult
from state import TaskState

logger = logging.getLogger(__name__)


async def request_human_review(db: Any, ctx: Any, task_id: str, question: str) -> SubmitTaskResult:
    """Pause task and ask user for input."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        """UPDATE agent_task SET status = 'human_review', updated_at = ?
           WHERE task_id = ? AND status = 'running'""",
        (time.time(), task_id),
    )
    await conn.commit()
    if not cursor.rowcount:
        return SubmitTaskResult(task_id=task_id, status="error", message="Task not running")
    cur = await conn.execute(
        "SELECT checkpoint, payload FROM agent_task WHERE task_id = ?", (task_id,)
    )
    row = await cur.fetchone()
    if row:
        checkpoint_raw, payload_raw = row[0], row[1]
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
        try:
            state = TaskState.from_json(checkpoint_raw) if checkpoint_raw else TaskState(goal=payload.get("goal", ""))
        except Exception:
            state = TaskState(goal=payload.get("goal", ""))
        state.context = dict(state.context)
        state.context["review_question"] = question
        await conn.execute(
            "UPDATE agent_task SET checkpoint = ?, updated_at = ? WHERE task_id = ?",
            (state.to_json(), time.time(), task_id),
        )
        await conn.commit()

    await ctx.notify_user(f"Task {task_id[:8]}... needs your input: {question}")
    return SubmitTaskResult(task_id=task_id, status="human_review", message="Task paused for review")


async def respond_to_review(db: Any, task_id: str, response: str) -> SubmitTaskResult:
    """Resume task with user's response."""
    conn = await db.ensure_conn()
    cur = await conn.execute(
        "SELECT checkpoint, payload FROM agent_task WHERE task_id = ? AND status = 'human_review'",
        (task_id,),
    )
    row = await cur.fetchone()
    if not row:
        return SubmitTaskResult(task_id=task_id, status="error", message="Task not in human_review")

    checkpoint_raw, payload_raw = row[0], row[1]
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
    try:
        state = TaskState.from_json(checkpoint_raw) if checkpoint_raw else TaskState(goal=payload.get("goal", ""))
    except Exception:
        state = TaskState(goal=payload.get("goal", ""))
    state.context = dict(state.context)
    state.context["review_response"] = response
    state.context.pop("review_question", None)

    await conn.execute(
        "UPDATE agent_task SET status = 'pending', checkpoint = ?, updated_at = ? WHERE task_id = ?",
        (state.to_json(), time.time(), task_id),
    )
    await conn.commit()
    return SubmitTaskResult(task_id=task_id, status="pending", message="Task resumed with user response")
