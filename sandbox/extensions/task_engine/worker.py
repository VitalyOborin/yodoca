"""Task Engine worker: claim, agent loop, lease renewal, retry logic."""

import asyncio
import json
import logging
import random
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.extensions.contract import AgentInvocationContext, AgentProvider

from models import StepRecord, TaskRecord
from state import TaskState, json_dumps_unicode

logger = logging.getLogger(__name__)

FINAL_MARKER = "<<TASK_COMPLETE>>"


@dataclass
class StepOutcome:
    """Normalized result of one step invocation (agent or orchestrator)."""

    content: str | None
    status: str  # "success" / "error" / "refused" for agent; "success" for orchestrator
    tokens_used: int | None = None
    error: str | None = None


_FINAL_RE = re.compile(r"^\s*<<TASK_COMPLETE>>[ \t]*", re.MULTILINE)


class RetryableError(Exception):
    """Task failed but can be retried (e.g. transient LLM error)."""


class NonRetryableError(Exception):
    """Task failed and should not be retried."""


class MaxStepsExceeded(Exception):
    """Task exceeded max_steps limit."""


class LeaseRevoked(Exception):
    """Lease was lost (another worker reclaimed or expiry)."""


class TaskCancelled(Exception):
    """Task was cancelled by user while running."""


def compute_retry_delay(attempt: int, base: float = 5.0, max_delay: float = 300.0) -> float:
    """Exponential backoff with jitter."""
    delay = min(base * (2**attempt), max_delay)
    jitter = random.uniform(0, delay * 0.3)
    return delay + jitter


def _extract_final_result(content: str) -> str | None:
    """Extract result if content contains <<TASK_COMPLETE>> marker at start of a line."""
    if not content:
        return None
    m = _FINAL_RE.search(content)
    if not m:
        return None
    return content[m.end():].strip() or None


def _build_step_prompt(state: TaskState, max_steps: int) -> str:
    """Build prompt for next step including task context."""
    parts = [
        f"Task goal: {state.goal}",
        f"Step {state.step + 1} of {max_steps}.",
    ]
    if state.context.get("subtask_results"):
        parts.append("Subtask results:")
        for sr in state.context["subtask_results"]:
            result_val = sr.get("result")
            if isinstance(result_val, dict):
                content = result_val.get("content", str(result_val))
            else:
                content = str(result_val) if result_val else "no result"
            parts.append(f"  - Task {sr.get('task_id', '?')}: {content[:300]}")
    if state.context.get("subtask_failures"):
        parts.append("Subtask failures:")
        for sf in state.context["subtask_failures"]:
            parts.append(f"  - Task {sf.get('task_id', '?')}: {sf.get('error', 'unknown error')}")
    if state.context.get("review_response"):
        parts.append(f"User review response: {state.context['review_response']}")
    if state.partial_result:
        parts.append(f"Progress so far:\n{state.partial_result}")
    parts.append(
        "Continue working on the task. When — and ONLY when — the task is fully complete, "
        f"your response MUST contain a line that starts with exactly: {FINAL_MARKER}\n"
        "followed by the final result on the same or subsequent lines. "
        f"Do NOT use {FINAL_MARKER} until the task is genuinely finished. "
        "If you are not done, respond with your progress and what you will do next."
    )
    return "\n\n".join(parts)


async def check_cancellation(db: Any, task_id: str) -> None:
    """Raise TaskCancelled if the task status was set to 'cancelled'."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        "SELECT status FROM agent_task WHERE task_id = ?", (task_id,)
    )
    row = await cursor.fetchone()
    if row and row[0] == "cancelled":
        raise TaskCancelled(f"Task {task_id} cancelled by user")


async def renew_lease(db: Any, task_id: str, worker_id: str, lease_ttl: float) -> bool:
    """Renew lease for task. Returns True if renewed, False if lease was lost."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        """
        UPDATE agent_task SET lease_exp = ?, updated_at = ?
        WHERE task_id = ? AND leased_by = ?
        """,
        (time.time() + lease_ttl, time.time(), task_id, worker_id),
    )
    await conn.commit()
    return cursor.rowcount > 0


@asynccontextmanager
async def _lease_keepalive(db: Any, task_id: str, worker_id: str, lease_ttl: float):
    """Background lease renewal while a long step executes."""
    renewal_interval = max(lease_ttl / 3, 10.0)
    stop = asyncio.Event()

    async def _renew_loop() -> None:
        while not stop.is_set():
            await asyncio.sleep(renewal_interval)
            if not stop.is_set():
                await renew_lease(db, task_id, worker_id, lease_ttl)

    task = asyncio.create_task(_renew_loop())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def claim_next_task(
    db: Any, worker_id: str, lease_ttl: float
) -> TaskRecord | None:
    """Claim next available task using CAS. Returns None if none available."""
    conn = await db.ensure_conn()
    now = time.time()

    cursor = await conn.execute(
        """
        SELECT task_id FROM agent_task
        WHERE status IN ('pending', 'retry_scheduled')
          AND (schedule_at IS NULL OR schedule_at <= ?)
          AND (lease_exp IS NULL OR lease_exp < ?)
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
        """,
        (now, now),
    )
    row = await cursor.fetchone()
    if not row:
        return None

    task_id = row[0]
    cursor = await conn.execute(
        """
        UPDATE agent_task
        SET status = 'running', leased_by = ?, lease_exp = ?, updated_at = ?
        WHERE task_id = ? AND status IN ('pending', 'retry_scheduled')
        """,
        (worker_id, now + lease_ttl, now, task_id),
    )
    await conn.commit()
    if cursor.rowcount == 0:
        return None

    return await _load_task(db, task_id)


async def _load_task(db: Any, task_id: str) -> TaskRecord | None:
    """Load task by id."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        "SELECT * FROM agent_task WHERE task_id = ?", (task_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    columns = [d[0] for d in cursor.description]
    d = dict(zip(columns, row))
    payload = json.loads(d["payload"]) if isinstance(d["payload"], str) else d["payload"]
    result = None
    if d["result"]:
        result = json.loads(d["result"]) if isinstance(d["result"], str) else d["result"]
    return TaskRecord(
        task_id=d["task_id"],
        parent_id=d["parent_id"],
        run_id=d["run_id"],
        agent_id=d["agent_id"],
        status=d["status"],
        priority=d["priority"] or 5,
        payload=payload,
        result=result,
        checkpoint=d["checkpoint"],
        error=d["error"],
        attempt_no=d["attempt_no"] or 0,
        schedule_at=d["schedule_at"],
        leased_by=d["leased_by"],
        lease_exp=d["lease_exp"],
        created_at=d["created_at"] or 0,
        updated_at=d["updated_at"] or 0,
    )


async def save_checkpoint(db: Any, task_id: str, state: TaskState) -> None:
    """Save TaskState to agent_task.checkpoint."""
    conn = await db.ensure_conn()
    await conn.execute(
        "UPDATE agent_task SET checkpoint = ?, updated_at = ? WHERE task_id = ?",
        (state.to_json(), time.time(), task_id),
    )
    await conn.commit()


async def save_step(db: Any, step: StepRecord) -> None:
    """Insert task_step row."""
    conn = await db.ensure_conn()
    await conn.execute(
        """
        INSERT INTO task_step (step_id, task_id, step_no, step_type, status,
                               tokens_used, duration_ms, error_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            step.step_id,
            step.task_id,
            step.step_no,
            step.step_type,
            step.status,
            step.tokens_used,
            step.duration_ms,
            step.error_code,
        ),
    )
    await conn.commit()


async def recover_stale_tasks(db: Any) -> int:
    """Reset running tasks with expired leases to pending. Returns count reset."""
    conn = await db.ensure_conn()
    now = time.time()
    cursor = await conn.execute(
        """
        UPDATE agent_task SET status = 'pending', leased_by = NULL, lease_exp = NULL
        WHERE status = 'running' AND (lease_exp IS NULL OR lease_exp < ?)
        """,
        (now,),
    )
    await conn.commit()
    return cursor.rowcount or 0


async def _run_step(
    invoke_fn: Callable[[], Awaitable[StepOutcome]],
    state: TaskState,
    task: TaskRecord,
    step_num: int,
    max_steps: int,
    db: Any,
    ctx: Any,
    worker_id: str,
    lease_ttl: float,
) -> dict | None:
    """Execute one step: invoke, record, checkpoint. Returns result dict if final, else None."""
    if not await renew_lease(db, task.task_id, worker_id, lease_ttl):
        raise LeaseRevoked(f"Lease lost for task {task.task_id}")
    await check_cancellation(db, task.task_id)

    t0 = time.monotonic()
    async with _lease_keepalive(db, task.task_id, worker_id, lease_ttl):
        outcome = await invoke_fn()
    duration_ms = int((time.monotonic() - t0) * 1000)

    error_code = None
    if outcome.status != "success" and outcome.error:
        error_code = (outcome.error[:100]) if len(outcome.error) > 100 else outcome.error
    step_record = StepRecord(
        step_id=f"{task.task_id}-{step_num}",
        task_id=task.task_id,
        step_no=step_num,
        step_type="llm_call",
        status="done" if outcome.status == "success" else "failed",
        tokens_used=outcome.tokens_used,
        duration_ms=duration_ms,
        error_code=error_code,
    )
    await save_step(db, step_record)
    logger.info(
        "task_step: %s",
        json_dumps_unicode(
            {
                "task_id": task.task_id,
                "step": step_num,
                "type": step_record.step_type,
                "status": step_record.status,
                "tokens": step_record.tokens_used,
                "duration_ms": duration_ms,
                "agent_id": task.agent_id,
                "run_id": task.run_id,
            }
        ),
    )

    if outcome.status == "error":
        raise RetryableError(outcome.error or "Agent step failed")
    if outcome.status == "refused":
        raise NonRetryableError(outcome.error or "Agent refused task")

    state.step = step_num + 1
    state.partial_result = outcome.content
    state.steps_log.append(
        {"step": state.step, "type": "llm_call", "summary": (outcome.content or "")[:200]}
    )
    await save_checkpoint(db, task.task_id, state)

    final_result = _extract_final_result(outcome.content or "")
    if final_result is not None:
        return {"content": final_result}

    await ctx.emit("task.progress", {"task_id": task.task_id, "step": state.step, "max_steps": max_steps})
    return None


async def execute_task(
    db: Any,
    ctx: Any,
    task: TaskRecord,
    get_agent: Callable[[str], AgentProvider | None],
    worker_id: str,
    lease_ttl: float,
    max_retries: int,
) -> None:
    """Run one task: load state, run agent/orchestrator loop, update status, handle errors."""
    state = TaskState(goal=task.payload.get("goal", ""))
    if task.checkpoint:
        try:
            state = TaskState.from_json(task.checkpoint)
        except Exception as e:
            logger.warning("task_engine: invalid checkpoint for %s: %s", task.task_id, e)

    try:
        if task.agent_id == "orchestrator":
            result = await run_orchestrator_loop(
                state, task, db, ctx, worker_id, lease_ttl
            )
        else:
            agent = get_agent(task.agent_id) if callable(get_agent) else None
            if not agent:
                raise NonRetryableError(f"Unknown agent: {task.agent_id}")
            result = await run_agent_loop(
                agent, state, task, db, ctx, worker_id, lease_ttl
            )

        conn = await db.ensure_conn()
        cursor = await conn.execute("SELECT status FROM agent_task WHERE task_id = ?", (task.task_id,))
        row = await cursor.fetchone()
        if row and row[0] in ("waiting_subtasks", "human_review"):
            await conn.execute(
                "UPDATE agent_task SET leased_by = NULL, lease_exp = NULL, updated_at = ? WHERE task_id = ?",
                (time.time(), task.task_id),
            )
            await conn.commit()
            return

        await conn.execute(
            "UPDATE agent_task SET status = 'done', result = ?, error = NULL, updated_at = ? WHERE task_id = ?",
            (json_dumps_unicode(result), time.time(), task.task_id),
        )
        await conn.commit()
        await ctx.emit("task.completed", {"task_id": task.task_id, "parent_id": task.parent_id, "status": "done", "result": result})

    except RetryableError as e:
        conn = await db.ensure_conn()
        exhausted = task.attempt_no + 1 >= max_retries
        if exhausted:
            new_status = "failed"
            schedule_at = None
        else:
            new_status = "retry_scheduled"
            schedule_at = time.time() + compute_retry_delay(task.attempt_no)
        await conn.execute(
            """
            UPDATE agent_task SET status = ?, attempt_no = ?, schedule_at = ?, error = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (new_status, task.attempt_no + 1, schedule_at, str(e), time.time(), task.task_id),
        )
        await conn.commit()
        if exhausted:
            await ctx.emit("task.completed", {"task_id": task.task_id, "parent_id": task.parent_id, "status": "failed", "error": str(e)})
        logger.warning("task_engine: task %s %s (attempt %d): %s", task.task_id, new_status, task.attempt_no + 1, e)

    except TaskCancelled:
        logger.info("task_engine: task %s cancelled during execution", task.task_id)
        await ctx.emit("task.completed", {"task_id": task.task_id, "parent_id": task.parent_id, "status": "cancelled", "error": "Cancelled by user"})

    except (NonRetryableError, LeaseRevoked) as e:
        conn = await db.ensure_conn()
        await conn.execute(
            "UPDATE agent_task SET status = 'failed', error = ?, updated_at = ? WHERE task_id = ?",
            (str(e), time.time(), task.task_id),
        )
        await conn.commit()
        await ctx.emit("task.completed", {"task_id": task.task_id, "parent_id": task.parent_id, "status": "failed", "error": str(e)})
        logger.warning("task_engine: task %s failed: %s", task.task_id, e)

    except Exception as e:
        conn = await db.ensure_conn()
        await conn.execute(
            "UPDATE agent_task SET status = 'failed', error = ?, updated_at = ? WHERE task_id = ?",
            (str(e), time.time(), task.task_id),
        )
        await conn.commit()
        await ctx.emit("task.completed", {"task_id": task.task_id, "parent_id": task.parent_id, "status": "failed", "error": str(e)})
        logger.exception("task_engine: task %s failed: %s", task.task_id, e)


async def run_agent_loop(
    agent: AgentProvider,
    state: TaskState,
    task: TaskRecord,
    db: Any,
    ctx: Any,
    worker_id: str,
    lease_ttl: float,
) -> dict:
    """Run ReAct loop for AgentProvider. Returns {'content': str} or raises."""
    max_steps = task.payload.get("max_steps", 20)
    step_context = AgentInvocationContext(
        conversation_summary=state.partial_result,
        correlation_id=task.run_id,
    )

    async def invoke_fn() -> StepOutcome:
        response = await agent.invoke(_build_step_prompt(state, max_steps), step_context)
        return StepOutcome(
            content=response.content,
            status=response.status,
            tokens_used=response.tokens_used,
            error=response.error,
        )

    for step_num in range(state.step, max_steps):
        result = await _run_step(
            invoke_fn, state, task, step_num, max_steps, db, ctx, worker_id, lease_ttl
        )
        if result is not None:
            return result

    return {
        "content": state.partial_result or "",
        "warning": f"Reached max_steps ({max_steps}) without completion signal",
    }


async def run_orchestrator_loop(
    state: TaskState,
    task: TaskRecord,
    db: Any,
    ctx: Any,
    worker_id: str,
    lease_ttl: float,
) -> dict:
    """Run ReAct loop for orchestrator (ctx.invoke_agent_background). Returns {'content': str} or raises."""
    max_steps = task.payload.get("max_steps", 20)

    async def invoke_fn() -> StepOutcome:
        content = await ctx.invoke_agent_background(_build_step_prompt(state, max_steps))
        return StepOutcome(content=content, status="success")

    for step_num in range(state.step, max_steps):
        result = await _run_step(
            invoke_fn, state, task, step_num, max_steps, db, ctx, worker_id, lease_ttl
        )
        if result is not None:
            return result

    return {
        "content": state.partial_result or "",
        "warning": f"Reached max_steps ({max_steps}) without completion signal",
    }
