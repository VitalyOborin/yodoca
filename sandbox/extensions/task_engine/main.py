"""Task Engine extension: ServiceProvider + ToolProvider for multi-step background agent work."""

import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import function_tool

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from models import (
    ActiveTasksResult,
    CancelTaskResult,
    SubmitTaskResult,
    TaskRecord,
    TaskStatusResult,
)
from schema import TaskEngineDb
from state import TaskState
from worker import (
    LeaseRevoked,
    NonRetryableError,
    RetryableError,
    claim_next_task,
    compute_retry_delay,
    recover_stale_tasks,
    run_agent_loop,
    run_orchestrator_loop,
)

if TYPE_CHECKING:
    from core.extensions.contract import AgentProvider
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)

MAX_SUBTASK_DEPTH = 3


class TaskEngineExtension:
    """ServiceProvider + ToolProvider: task queue, worker loop, tools for Orchestrator."""

    def __init__(self) -> None:
        self._ctx: "ExtensionContext | None" = None
        self._db: TaskEngineDb | None = None
        self._agent_registry: dict[str, "AgentProvider"] = {}
        self._tick_sec: float = 1.0
        self._lease_ttl: float = 90.0
        self._max_retries: int = 5
        self._worker_id: str = ""

    async def initialize(self, context: "ExtensionContext") -> None:
        self._ctx = context
        self._worker_id = str(uuid.uuid4())[:8]
        self._tick_sec = float(context.get_config("tick_sec", 1.0))
        self._lease_ttl = float(context.get_config("lease_ttl_sec", 90.0))
        self._max_retries = int(context.get_config("max_retries", 5))

        db_path = context.data_dir / "task_engine.db"
        self._db = TaskEngineDb(db_path)
        await self._db.ensure_conn()

        for ext_id in context.get_config("agent_extensions") or []:
            try:
                provider = context.get_extension(ext_id)
                if provider and hasattr(provider, "get_agent_descriptor"):
                    descriptor = provider.get_agent_descriptor()
                    self._agent_registry[descriptor.name] = provider
                    logger.info("task_engine: registered agent %s (%s)", descriptor.name, ext_id)
            except Exception as e:
                logger.warning("task_engine: could not load agent %s: %s", ext_id, e)

        context.subscribe_event("task.completed", self._on_task_completed)

    async def _on_task_completed(self, event: Any) -> None:
        """Phase 3: subtask resume and top-level user notification."""
        payload = getattr(event, "payload", event) if hasattr(event, "payload") else {}
        if not isinstance(payload, dict):
            return
        parent_id = payload.get("parent_id")
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")

        if parent_id:
            await self._try_resume_parent(parent_id)

        if not parent_id and status in ("done", "failed"):
            await self._notify_task_completed(task_id, status, payload)

    async def _get_subtask_depth(self, task_id: str) -> int:
        """Count ancestor depth by walking parent_id chain."""
        if not self._db:
            return 0
        conn = await self._db.ensure_conn()
        conn.row_factory = None
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

    async def _update_parent_checkpoint(self, parent_task_id: str, child_task_id: str) -> None:
        """Append child task_id to parent's pending_subtasks in checkpoint."""
        if not self._db:
            return
        conn = await self._db.ensure_conn()
        conn.row_factory = None
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

    async def _try_resume_parent(self, parent_id: str) -> None:
        """If all siblings are terminal, inject results into parent and set parent to pending."""
        if not self._db:
            return
        conn = await self._db.ensure_conn()
        conn.row_factory = None
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

        results, failures = await self._collect_subtask_results(parent_id)
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

    async def _collect_subtask_results(self, parent_id: str) -> tuple[list[dict], list[dict]]:
        """Query all children of parent, return (results, failures) tuples."""
        if not self._db:
            return ([], [])
        conn = await self._db.ensure_conn()
        conn.row_factory = None
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

    async def _notify_task_completed(self, task_id: str, status: str, payload: dict) -> None:
        """Emit system.user.notify for top-level task completion."""
        if not self._ctx:
            return
        if status == "done":
            result = payload.get("result")
            if isinstance(result, dict):
                content = result.get("content", str(result))
            else:
                content = str(result) if result else "Task completed."
            summary = f"Task {task_id[:8]}... completed: {content[:500]}"
        else:
            error = payload.get("error", "unknown")
            summary = f"Task {task_id[:8]}... failed: {error}"
        await self._ctx.notify_user(summary)

    async def _request_human_review(self, task_id: str, question: str) -> SubmitTaskResult:
        """Pause task and ask user for input."""
        if not self._db or not self._ctx:
            return SubmitTaskResult(task_id=task_id, status="error", message="Not initialized")
        conn = await self._db.ensure_conn()
        cursor = await conn.execute(
            """UPDATE agent_task SET status = 'human_review', updated_at = ?
               WHERE task_id = ? AND status = 'running'""",
            (time.time(), task_id),
        )
        await conn.commit()
        if not cursor.rowcount:
            return SubmitTaskResult(task_id=task_id, status="error", message="Task not running")

        conn.row_factory = None
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

        await self._ctx.notify_user(f"Task {task_id[:8]}... needs your input: {question}")
        return SubmitTaskResult(task_id=task_id, status="human_review", message="Task paused for review")

    async def _respond_to_review(self, task_id: str, response: str) -> SubmitTaskResult:
        """Resume task with user's response."""
        if not self._db:
            return SubmitTaskResult(task_id=task_id, status="error", message="Not initialized")
        conn = await self._db.ensure_conn()
        conn.row_factory = None
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

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        """SchedulerProvider: periodic cleanup of old done/failed/cancelled tasks."""
        if task_name != "cleanup_old_tasks" or not self._db or not self._ctx:
            return None
        retention_days = int(self._ctx.get_config("retention_days", 30))
        cutoff = time.time() - (retention_days * 86400)
        conn = await self._db.ensure_conn()
        cursor = await conn.execute(
            """
            DELETE FROM task_step WHERE task_id IN (
                SELECT task_id FROM agent_task
                WHERE status IN ('done', 'failed', 'cancelled')
                  AND updated_at < ?
            )
            """,
            (cutoff,),
        )
        steps_deleted = cursor.rowcount or 0
        cursor = await conn.execute(
            """
            DELETE FROM agent_task
            WHERE status IN ('done', 'failed', 'cancelled')
              AND updated_at < ?
              AND task_id NOT IN (
                  SELECT DISTINCT parent_id FROM agent_task WHERE parent_id IS NOT NULL
              )
            """,
            (cutoff,),
        )
        tasks_deleted = cursor.rowcount or 0
        await conn.commit()
        summary = f"Cleanup: deleted {tasks_deleted} tasks, {steps_deleted} steps (retention={retention_days}d)"
        logger.info("task_engine: %s", summary)
        return {"text": summary}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
        self._ctx = None
        self._agent_registry.clear()

    def health_check(self) -> bool:
        return self._db is not None and self._ctx is not None

    def get_tools(self) -> list[Any]:
        """Return tools for Orchestrator: submit_task, get_task_status, list_active_tasks, cancel_task."""
        if not self._ctx or not self._db:
            return []

        ext = self

        @function_tool
        async def submit_task(
            goal: str,
            agent_id: str = "orchestrator",
            priority: int = 5,
            parent_task_id: str | None = None,
            max_steps: int | None = None,
        ) -> SubmitTaskResult:
            """Submit a new background task for async execution by a specified agent.

            Use when:
            - The task requires multiple steps (research, generation, analysis)
            - The task should run in the background while the user continues chatting
            - The task needs a specialized agent (image_agent, code_agent, etc.)

            Returns task_id for tracking.
            """
            return await ext.submit_task(goal, agent_id, priority, parent_task_id, max_steps)

        @function_tool
        async def get_task_status(task_id: str) -> TaskStatusResult:
            """Get current status, progress, and partial result of a background task."""
            return await ext._get_task_status(task_id)

        @function_tool
        async def list_active_tasks() -> ActiveTasksResult:
            """List all running and pending tasks with statuses and progress."""
            return await ext._list_active_tasks()

        @function_tool
        async def cancel_task(task_id: str, reason: str = "") -> CancelTaskResult:
            """Cancel a running or pending task."""
            return await ext._cancel_task(task_id, reason)

        @function_tool
        async def request_human_review(task_id: str, question: str) -> SubmitTaskResult:
            """Pause a running task and ask the user for input.
            Use when the task needs a decision, clarification, or approval from the user.
            The task will be paused until the user responds."""
            return await ext._request_human_review(task_id, question)

        @function_tool
        async def respond_to_review(task_id: str, response: str) -> SubmitTaskResult:
            """Provide user's response to a paused task.
            Call when the user replies to a human_review question."""
            return await ext._respond_to_review(task_id, response)

        return [submit_task, get_task_status, list_active_tasks, cancel_task, request_human_review, respond_to_review]

    async def submit_task(
        self,
        goal: str,
        agent_id: str = "orchestrator",
        priority: int = 5,
        parent_task_id: str | None = None,
        max_steps: int | None = None,
    ) -> SubmitTaskResult:
        if not self._db or not self._ctx:
            return SubmitTaskResult(task_id="", status="error", message="Extension not initialized")

        if agent_id != "orchestrator" and agent_id not in self._agent_registry:
            return SubmitTaskResult(
                task_id="",
                status="error",
                message=f"Unknown agent: {agent_id}. Available: orchestrator, {', '.join(self._agent_registry.keys())}",
            )

        if parent_task_id:
            depth = await self._get_subtask_depth(parent_task_id)
            if depth >= MAX_SUBTASK_DEPTH:
                return SubmitTaskResult(
                    task_id="",
                    status="error",
                    message=f"Max subtask depth ({MAX_SUBTASK_DEPTH}) exceeded",
                )

        task_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        payload = {
            "goal": goal,
            "max_steps": max_steps or int(self._ctx.get_config("default_max_steps", 20)),
            "source": "orchestrator",
        }
        conn = await self._db.ensure_conn()
        now = time.time()
        await conn.execute(
            """
            INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (task_id, parent_task_id, run_id, agent_id, priority, json.dumps(payload), now, now),
        )

        if parent_task_id:
            await conn.execute(
                """
                UPDATE agent_task SET status = 'waiting_subtasks', updated_at = ?
                WHERE task_id = ? AND status = 'running'
                """,
                (now, parent_task_id),
            )
            await self._update_parent_checkpoint(parent_task_id, task_id)

        await conn.commit()

        await self._ctx.emit("task.submitted", {"task_id": task_id, "agent_id": agent_id, "goal": goal, "priority": priority})
        return SubmitTaskResult(task_id=task_id, status="pending", message=f"Task {task_id} queued")

    async def _get_task_status(self, task_id: str) -> TaskStatusResult:
        if not self._db:
            return TaskStatusResult(
                task_id=task_id, status="error", agent_id="", goal="", step=0, max_steps=0,
                attempt_no=0, error="Extension not initialized",
            )
        conn = await self._db.ensure_conn()
        conn.row_factory = None
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

    async def _list_active_tasks(self) -> ActiveTasksResult:
        if not self._db:
            return ActiveTasksResult(tasks=[], total=0)
        conn = await self._db.ensure_conn()
        conn.row_factory = None
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
            if d.get("checkpoint"):
                try:
                    state = TaskState.from_json(d["checkpoint"])
                    checkpoint = state.partial_result
                except Exception:
                    pass
            tasks.append(
                TaskStatusResult(
                    task_id=d["task_id"],
                    status=d["status"],
                    agent_id=d["agent_id"],
                    goal=payload.get("goal", ""),
                    step=0,
                    max_steps=payload.get("max_steps", 20),
                    attempt_no=d.get("attempt_no") or 0,
                    partial_result=checkpoint,
                    error=d.get("error"),
                    created_at=d.get("created_at") or 0,
                    updated_at=d.get("updated_at") or 0,
                )
            )
        return ActiveTasksResult(tasks=tasks, total=len(tasks))

    async def _cancel_task(self, task_id: str, reason: str = "") -> CancelTaskResult:
        if not self._db:
            return CancelTaskResult(task_id=task_id, status="error", message="Extension not initialized")
        conn = await self._db.ensure_conn()
        cursor = await conn.execute(
            "UPDATE agent_task SET status = 'cancelled', error = ? WHERE task_id = ? AND status IN ('pending', 'retry_scheduled')",
            (reason or "Cancelled by user", task_id),
        )
        await conn.commit()
        if cursor.rowcount:
            return CancelTaskResult(task_id=task_id, status="cancelled", message="Task cancelled")
        return CancelTaskResult(task_id=task_id, status="not_found", message="Task not found or already running/done")

    async def run_background(self) -> None:
        """ServiceProvider: worker loop. Claim tasks, execute, handle errors."""
        if not self._db or not self._ctx:
            return
        n = await recover_stale_tasks(self._db)
        if n:
            logger.info("task_engine: recovered %d stale tasks", n)
        while True:
            try:
                task = await claim_next_task(self._db, self._worker_id, self._lease_ttl)
                if task:
                    await self._execute_task(task)
                else:
                    await asyncio.sleep(self._tick_sec)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("task_engine worker error: %s", e)
                await asyncio.sleep(self._tick_sec)

    async def _execute_task(self, task: TaskRecord) -> None:
        if not self._db or not self._ctx:
            return
        state = TaskState(goal=task.payload.get("goal", ""))
        if task.checkpoint:
            try:
                state = TaskState.from_json(task.checkpoint)
            except Exception as e:
                logger.warning("task_engine: invalid checkpoint for %s: %s", task.task_id, e)

        try:
            if task.agent_id == "orchestrator":
                result = await run_orchestrator_loop(
                    state, task, self._db, self._ctx, self._worker_id, self._lease_ttl
                )
            else:
                agent = self._agent_registry.get(task.agent_id)
                if not agent:
                    raise NonRetryableError(f"Unknown agent: {task.agent_id}")
                result = await run_agent_loop(
                    agent, state, task, self._db, self._ctx, self._worker_id, self._lease_ttl
                )

            conn = await self._db.ensure_conn()
            conn.row_factory = None
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
                (json.dumps(result), time.time(), task.task_id),
            )
            await conn.commit()
            await self._ctx.emit("task.completed", {"task_id": task.task_id, "parent_id": task.parent_id, "status": "done", "result": result})

        except RetryableError as e:
            delay = compute_retry_delay(task.attempt_no)
            conn = await self._db.ensure_conn()
            await conn.execute(
                """
                UPDATE agent_task SET status = 'retry_scheduled', attempt_no = ?, schedule_at = ?, error = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (task.attempt_no + 1, time.time() + delay, str(e), time.time(), task.task_id),
            )
            await conn.commit()
            if task.attempt_no + 1 >= self._max_retries:
                await conn.execute(
                    "UPDATE agent_task SET status = 'failed' WHERE task_id = ?",
                    (task.task_id,),
                )
                await conn.commit()
                await self._ctx.emit("task.completed", {"task_id": task.task_id, "parent_id": task.parent_id, "status": "failed", "error": str(e)})
            logger.warning("task_engine: task %s retry scheduled (attempt %d): %s", task.task_id, task.attempt_no + 1, e)

        except (NonRetryableError, LeaseRevoked) as e:
            conn = await self._db.ensure_conn()
            await conn.execute(
                "UPDATE agent_task SET status = 'failed', error = ?, updated_at = ? WHERE task_id = ?",
                (str(e), time.time(), task.task_id),
            )
            await conn.commit()
            await self._ctx.emit("task.completed", {"task_id": task.task_id, "parent_id": task.parent_id, "status": "failed", "error": str(e)})
            logger.warning("task_engine: task %s failed: %s", task.task_id, e)

        except Exception as e:
            conn = await self._db.ensure_conn()
            await conn.execute(
                "UPDATE agent_task SET status = 'failed', error = ?, updated_at = ? WHERE task_id = ?",
                (str(e), time.time(), task.task_id),
            )
            await conn.commit()
            await self._ctx.emit("task.completed", {"task_id": task.task_id, "parent_id": task.parent_id, "status": "failed", "error": str(e)})
            logger.exception("task_engine: task %s failed: %s", task.task_id, e)
