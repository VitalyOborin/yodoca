"""Task Engine extension: ServiceProvider + ToolProvider for multi-step background agent work."""

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from sandbox.extensions.task_engine.chains import get_chain_tasks, unblock_successors
from sandbox.extensions.task_engine.cleanup import cleanup_old_tasks
from sandbox.extensions.task_engine.hitl import (
    request_human_review as hitl_request_human_review,
)
from sandbox.extensions.task_engine.hitl import (
    respond_to_review as hitl_respond_to_review,
)
from sandbox.extensions.task_engine.models import (
    ActiveTasksResult,
    CancelTaskResult,
    ChainStatusResult,
    ChainStep,
    ChainTaskInfo,
    SubmitChainResult,
    SubmitTaskResult,
    TaskStatusResult,
)
from sandbox.extensions.task_engine.schema import TaskEngineDb
from sandbox.extensions.task_engine.state import json_dumps_unicode
from sandbox.extensions.task_engine.subtasks import (
    MAX_SUBTASK_DEPTH,
    get_subtask_depth,
    try_resume_parent,
    update_parent_checkpoint,
)
from sandbox.extensions.task_engine.task_engine_tools import build_tools
from sandbox.extensions.task_engine.task_queries import (
    cancel_task as query_cancel_task,
)
from sandbox.extensions.task_engine.task_queries import (
    get_task_status as query_get_task_status,
)
from sandbox.extensions.task_engine.task_queries import (
    list_active_tasks as query_list_active_tasks,
)
from sandbox.extensions.task_engine.task_queries import list_tasks as query_list_tasks
from sandbox.extensions.task_engine.worker import (
    claim_next_task,
    execute_task,
    recover_stale_tasks,
)

if TYPE_CHECKING:
    from core.extensions.context import ExtensionContext
    from core.extensions.contract import AgentProvider

logger = logging.getLogger(__name__)


class TaskEngineExtensionConfig(BaseModel):
    """Merged manifest config + settings.extensions.task_engine overrides."""

    model_config = ConfigDict(extra="forbid")

    tick_sec: float = 1.0
    max_concurrent_tasks: int = 3
    lease_ttl_sec: float = 90.0
    max_retries: int = 5
    default_max_steps: int = 20
    step_timeout_sec: int = 120
    retention_days: int = 30


class TaskEngineExtension:
    """ServiceProvider + ToolProvider: task queue, worker loop, tools for Orchestrator."""

    ConfigModel = TaskEngineExtensionConfig

    def __init__(self) -> None:
        self._ctx: ExtensionContext | None = None
        self._db: TaskEngineDb | None = None
        self._registry: Any = None
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

        self._registry = context.agent_registry

        context.subscribe_event("task.completed", self._on_task_completed)

    async def _on_task_completed(self, event: Any) -> None:
        """Phase 3: subtask resume and top-level user notification."""
        payload = getattr(event, "payload", event) if hasattr(event, "payload") else {}
        if not isinstance(payload, dict):
            return
        parent_id = payload.get("parent_id")
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")

        if parent_id and self._db:
            await try_resume_parent(self._db, parent_id)

        if self._db:
            await unblock_successors(self._db, task_id, status, payload.get("result"))

        if not parent_id and status in ("done", "failed"):
            await self._notify_task_completed(task_id, status, payload)

    async def _task_exists(self, task_id: str) -> bool:
        """Check if a task exists."""
        if not self._db:
            return False
        conn = await self._db.ensure_conn()
        cursor = await conn.execute(
            "SELECT 1 FROM agent_task WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        return row is not None

    async def _get_subtask_depth(self, task_id: str) -> int:
        """Count ancestor depth by walking parent_id chain."""
        if not self._db:
            return 0
        return await get_subtask_depth(self._db, task_id)

    async def _update_parent_checkpoint(
        self, parent_task_id: str, child_task_id: str
    ) -> None:
        """Append child task_id to parent's pending_subtasks in checkpoint."""
        if not self._db:
            return
        await update_parent_checkpoint(self._db, parent_task_id, child_task_id)

    async def _notify_task_completed(
        self, task_id: str, status: str, payload: dict
    ) -> None:
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

    async def _request_human_review(
        self, task_id: str, question: str
    ) -> SubmitTaskResult:
        """Pause task and ask user for input."""
        if not self._db or not self._ctx:
            return SubmitTaskResult(
                task_id=task_id, status="error", message="Not initialized"
            )
        return await hitl_request_human_review(self._db, self._ctx, task_id, question)

    async def _respond_to_review(self, task_id: str, response: str) -> SubmitTaskResult:
        """Resume task with user's response."""
        if not self._db:
            return SubmitTaskResult(
                task_id=task_id, status="error", message="Not initialized"
            )
        return await hitl_respond_to_review(self._db, task_id, response)

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        """SchedulerProvider: periodic cleanup of old done/failed/cancelled tasks."""
        if task_name != "cleanup_old_tasks" or not self._db or not self._ctx:
            return None
        retention_days = int(self._ctx.get_config("retention_days", 30))
        return await cleanup_old_tasks(self._db, retention_days)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
        self._ctx = None
        self._registry = None

    def health_check(self) -> bool:
        return self._db is not None and self._ctx is not None

    def get_tools(self) -> list[Any]:
        """Return tools for Orchestrator: submit_task, get_task_status, list_active_tasks, cancel_task."""
        if not self._ctx or not self._db:
            return []
        return build_tools(self)

    async def submit_task(
        self,
        goal: str,
        agent_id: str = "orchestrator",
        priority: int = 5,
        parent_task_id: str | None = None,
        after_task_id: str | None = None,
        max_steps: int | None = None,
        output_channel: str | None = None,
    ) -> SubmitTaskResult:
        if not self._db or not self._ctx:
            return SubmitTaskResult(
                task_id="", status="error", message="Extension not initialized"
            )

        if agent_id != "orchestrator":
            pair = self._registry.get(agent_id) if self._registry else None
            if not pair:
                available = (
                    [r.id for r in self._registry.list_agents()]
                    if self._registry
                    else []
                )
                avail_str = ", ".join(["orchestrator"] + available)
                return SubmitTaskResult(
                    task_id="",
                    status="error",
                    message=f"Unknown agent: {agent_id}. Available: {avail_str}",
                )

        if parent_task_id:
            depth = await self._get_subtask_depth(parent_task_id)
            if depth >= MAX_SUBTASK_DEPTH:
                return SubmitTaskResult(
                    task_id="",
                    status="error",
                    message=f"Max subtask depth ({MAX_SUBTASK_DEPTH}) exceeded",
                )

        if after_task_id:
            exists = await self._task_exists(after_task_id)
            if not exists:
                return SubmitTaskResult(
                    task_id="",
                    status="error",
                    message=f"Predecessor task {after_task_id} not found",
                )

        task_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        payload = {
            "goal": goal,
            "max_steps": max_steps
            or int(self._ctx.get_config("default_max_steps", 20)),
            "source": "orchestrator",
            "output_channel": output_channel,
        }
        initial_status = "blocked" if after_task_id else "pending"
        conn = await self._db.ensure_conn()
        now = int(time.time())
        await conn.execute(
            """
            INSERT INTO agent_task (
                task_id, parent_id, run_id, agent_id, status, priority, payload,
                created_at, updated_at, after_task_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                parent_task_id,
                run_id,
                agent_id,
                initial_status,
                priority,
                json_dumps_unicode(payload),
                now,
                now,
                after_task_id,
            ),
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

        await self._ctx.emit(
            "task.submitted",
            {
                "task_id": task_id,
                "agent_id": agent_id,
                "goal": goal,
                "priority": priority,
            },
        )
        return SubmitTaskResult(
            task_id=task_id, status="pending", message=f"Task {task_id} queued"
        )

    async def _get_task_status(self, task_id: str) -> TaskStatusResult:
        if not self._db:
            return TaskStatusResult(
                task_id=task_id,
                status="error",
                agent_id="",
                goal="",
                step=0,
                max_steps=0,
                attempt_no=0,
                error="Extension not initialized",
            )
        return await query_get_task_status(self._db, task_id)

    async def _list_active_tasks(self) -> ActiveTasksResult:
        if not self._db:
            return ActiveTasksResult(tasks=[], total=0)
        return await query_list_active_tasks(self._db)

    async def list_tasks(self, status: str = "active") -> ActiveTasksResult:
        """Public API for list tasks with optional status filter."""
        if not self._db:
            return ActiveTasksResult(tasks=[], total=0)
        return await query_list_tasks(self._db, status=status)

    async def get_task(self, task_id: str) -> TaskStatusResult:
        """Public API for get task status/details."""
        return await self._get_task_status(task_id)

    def _get_agent_provider(self, agent_id: str) -> "AgentProvider | None":
        """Resolve agent_id to AgentProvider via registry."""
        pair = self._registry.get(agent_id) if self._registry else None
        return pair[1] if pair else None

    async def _cancel_task(self, task_id: str, reason: str = "") -> CancelTaskResult:
        """Cancel a task. Works on pending, running, waiting, and human_review tasks."""
        if not self._db:
            return CancelTaskResult(
                task_id=task_id, status="error", message="Extension not initialized"
            )
        return await query_cancel_task(self._db, task_id, reason)

    async def cancel_task(self, task_id: str, reason: str = "") -> CancelTaskResult:
        """Public API for cancel task."""
        return await self._cancel_task(task_id, reason)

    async def submit_chain(
        self,
        steps: list[ChainStep],
        priority: int = 5,
        output_channel: str | None = None,
    ) -> SubmitChainResult:
        """Submit a sequence of tasks that execute one after another (ADR 018)."""
        if not self._db or not self._ctx:
            return SubmitChainResult(
                chain_id="",
                tasks=[],
                message="Extension not initialized",
            )
        if not steps:
            return SubmitChainResult(
                chain_id="",
                tasks=[],
                message="At least one step required",
            )

        max_steps = int(self._ctx.get_config("default_max_steps", 20))
        default_max = max_steps

        for step in steps:
            if step.agent_id != "orchestrator" and self._registry:
                pair = self._registry.get(step.agent_id)
                if not pair:
                    available = [r.id for r in self._registry.list_agents()]
                    avail_str = ", ".join(["orchestrator"] + available)
                    return SubmitChainResult(
                        chain_id="",
                        tasks=[],
                        message=f"Unknown agent in step: {step.agent_id}. Available: {avail_str}",
                    )

        chain_id = str(uuid.uuid4())
        conn = await self._db.ensure_conn()
        now = int(time.time())
        task_infos: list[ChainTaskInfo] = []
        prev_task_id: str | None = None

        for i, step in enumerate(steps):
            task_id = str(uuid.uuid4())
            run_id = str(uuid.uuid4())
            status = "pending" if prev_task_id is None else "blocked"
            payload = {
                "goal": step.goal,
                "max_steps": default_max,
                "source": "orchestrator",
                "output_channel": output_channel,
            }
            await conn.execute(
                """
                INSERT INTO agent_task (
                    task_id, parent_id, run_id, agent_id, status, priority,
                    payload, created_at, updated_at, after_task_id, chain_id, chain_order
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    run_id,
                    step.agent_id,
                    status,
                    priority,
                    json_dumps_unicode(payload),
                    now,
                    now,
                    prev_task_id,
                    chain_id,
                    i,
                ),
            )
            task_infos.append(
                ChainTaskInfo(
                    task_id=task_id,
                    goal=step.goal,
                    agent_id=step.agent_id,
                    chain_order=i,
                    status=status,
                )
            )
            await self._ctx.emit(
                "task.submitted",
                {
                    "task_id": task_id,
                    "agent_id": step.agent_id,
                    "goal": step.goal,
                    "priority": priority,
                },
            )
            prev_task_id = task_id

        await conn.commit()
        return SubmitChainResult(
            chain_id=chain_id,
            tasks=task_infos,
            message=f"Chain {chain_id[:8]}... queued with {len(steps)} step(s)",
        )

    async def _get_chain_status(self, chain_id: str) -> ChainStatusResult:
        """Get status of all tasks in a chain (ADR 018)."""
        if not self._db:
            return ChainStatusResult(
                chain_id=chain_id,
                status="error",
                tasks=[],
            )
        rows = await get_chain_tasks(self._db, chain_id)
        if not rows:
            return ChainStatusResult(
                chain_id=chain_id,
                status="not_found",
                tasks=[],
            )
        task_infos = [
            ChainTaskInfo(
                task_id=r["task_id"],
                goal=r["goal"],
                agent_id=r["agent_id"],
                chain_order=r.get("chain_order", i),
                status=r["status"],
            )
            for i, r in enumerate(rows)
        ]
        statuses = [r["status"] for r in rows]
        if all(s == "done" for s in statuses):
            overall = "done"
        elif any(s == "failed" for s in statuses):
            overall = "failed"
        elif any(s == "cancelled" for s in statuses):
            overall = "cancelled"
        elif any(s in ("running", "pending") for s in statuses):
            overall = "running"
        else:
            overall = "blocked"
        return ChainStatusResult(
            chain_id=chain_id,
            status=overall,
            tasks=task_infos,
        )

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
                    await execute_task(
                        self._db,
                        self._ctx,
                        task,
                        self._get_agent_provider,
                        self._worker_id,
                        self._lease_ttl,
                        self._max_retries,
                    )
                else:
                    await asyncio.sleep(self._tick_sec)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("task_engine worker error: %s", e)
                await asyncio.sleep(self._tick_sec)
