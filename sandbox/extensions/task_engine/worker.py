"""Task Engine worker: claim, agent loop, lease renewal, retry logic."""

import asyncio
import json
import logging
import random
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

from core.extensions.contract import AgentInvocationContext, AgentProvider

from models import StepRecord, TaskRecord
from state import TaskState

logger = logging.getLogger(__name__)

FINAL_PREFIX = "FINAL:"


class RetryableError(Exception):
    """Task failed but can be retried (e.g. transient LLM error)."""


class NonRetryableError(Exception):
    """Task failed and should not be retried."""


class MaxStepsExceeded(Exception):
    """Task exceeded max_steps limit."""


class LeaseRevoked(Exception):
    """Lease was lost (another worker reclaimed or expiry)."""


def compute_retry_delay(attempt: int, base: float = 5.0, max_delay: float = 300.0) -> float:
    """Exponential backoff with jitter."""
    delay = min(base * (2**attempt), max_delay)
    jitter = random.uniform(0, delay * 0.3)
    return delay + jitter


def _extract_final_result(content: str) -> str | None:
    """Extract result if content contains FINAL: prefix. Returns None if not final."""
    if not content or FINAL_PREFIX not in content:
        return None
    idx = content.find(FINAL_PREFIX)
    return content[idx + len(FINAL_PREFIX) :].strip()


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
        "Continue working on the task. When you have completed it, your final response "
        f"MUST include a line starting with exactly: {FINAL_PREFIX} followed by your result. "
        "If you are not done, respond with your progress and what you'll do next."
    )
    return "\n\n".join(parts)


async def renew_lease(db: Any, task_id: str, worker_id: str, lease_ttl: float) -> bool:
    """Renew lease for task. Returns True if renewed, False if lease was lost."""
    conn = await db.ensure_conn()
    conn.row_factory = None
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
    conn.row_factory = None
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
    conn.row_factory = None
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

    for step_num in range(state.step, max_steps):
        if not await renew_lease(db, task.task_id, worker_id, lease_ttl):
            raise LeaseRevoked(f"Lease lost for task {task.task_id}")

        step_prompt = _build_step_prompt(state, max_steps)

        t0 = time.monotonic()
        async with _lease_keepalive(db, task.task_id, worker_id, lease_ttl):
            response = await agent.invoke(step_prompt, step_context)
        duration_ms = int((time.monotonic() - t0) * 1000)

        error_code = None
        if response.status != "success" and response.error:
            error_code = (response.error[:100]) if len(response.error) > 100 else response.error
        step_record = StepRecord(
            step_id=f"{task.task_id}-{step_num}",
            task_id=task.task_id,
            step_no=step_num,
            step_type="llm_call",
            status="done" if response.status == "success" else "failed",
            tokens_used=response.tokens_used,
            duration_ms=duration_ms,
            error_code=error_code,
        )
        await save_step(db, step_record)
        logger.info(
            "task_step: %s",
            json.dumps(
                {
                    "task_id": task.task_id,
                    "step": step_num,
                    "type": step_record.step_type,
                    "status": step_record.status,
                    "tokens": step_record.tokens_used,
                    "duration_ms": duration_ms,
                    "agent_id": task.agent_id,
                    "run_id": task.run_id,
                },
                ensure_ascii=False,
            ),
        )

        if response.status == "error":
            raise RetryableError(response.error or "Agent step failed")
        if response.status == "refused":
            raise NonRetryableError(response.error or "Agent refused task")

        state.step = step_num + 1
        state.partial_result = response.content
        state.steps_log.append(
            {"step": state.step, "type": "llm_call", "summary": (response.content or "")[:200]}
        )
        await save_checkpoint(db, task.task_id, state)

        final_result = _extract_final_result(response.content or "")
        if final_result is not None:
            return {"content": final_result}

        await ctx.emit("task.progress", {"task_id": task.task_id, "step": state.step, "max_steps": max_steps})

    return {
        "content": state.partial_result or "",
        "warning": f"Reached max_steps ({max_steps}) without FINAL: signal",
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

    for step_num in range(state.step, max_steps):
        if not await renew_lease(db, task.task_id, worker_id, lease_ttl):
            raise LeaseRevoked(f"Lease lost for task {task.task_id}")

        step_prompt = _build_step_prompt(state, max_steps)

        t0 = time.monotonic()
        async with _lease_keepalive(db, task.task_id, worker_id, lease_ttl):
            content = await ctx.invoke_agent_background(step_prompt)
        duration_ms = int((time.monotonic() - t0) * 1000)

        step_record = StepRecord(
            step_id=f"{task.task_id}-{step_num}",
            task_id=task.task_id,
            step_no=step_num,
            step_type="llm_call",
            status="done",
            tokens_used=None,
            duration_ms=duration_ms,
            error_code=None,
        )
        await save_step(db, step_record)
        logger.info(
            "task_step: %s",
            json.dumps(
                {
                    "task_id": task.task_id,
                    "step": step_num,
                    "type": step_record.step_type,
                    "status": step_record.status,
                    "tokens": step_record.tokens_used,
                    "duration_ms": duration_ms,
                    "agent_id": task.agent_id,
                    "run_id": task.run_id,
                },
                ensure_ascii=False,
            ),
        )

        state.step = step_num + 1
        state.partial_result = content
        state.steps_log.append(
            {"step": state.step, "type": "llm_call", "summary": (content or "")[:200]}
        )
        await save_checkpoint(db, task.task_id, state)

        final_result = _extract_final_result(content or "")
        if final_result is not None:
            return {"content": final_result}

        await ctx.emit("task.progress", {"task_id": task.task_id, "step": state.step, "max_steps": max_steps})

    return {
        "content": state.partial_result or "",
        "warning": f"Reached max_steps ({max_steps}) without FINAL: signal",
    }
