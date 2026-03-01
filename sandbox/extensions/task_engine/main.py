"""Task Engine extension: ServiceProvider + ToolProvider for multi-step background agent work."""

import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

_ext_dir = Path(__file__).resolve().parent
if str(_ext_dir) not in sys.path:
    sys.path.insert(0, str(_ext_dir))

from models import (
    ActiveTasksResult,
    CancelTaskResult,
    SubmitTaskResult,
    TaskStatusResult,
)
from schema import TaskEngineDb
from state import json_dumps_unicode
from cleanup import cleanup_old_tasks
from hitl import request_human_review as hitl_request_human_review
from hitl import respond_to_review as hitl_respond_to_review
from subtasks import (
    MAX_SUBTASK_DEPTH,
    get_subtask_depth,
    try_resume_parent,
    update_parent_checkpoint,
)
from task_queries import cancel_task as query_cancel_task
from task_queries import get_task_status as query_get_task_status
from task_queries import list_active_tasks as query_list_active_tasks
from task_engine_tools import build_tools
from worker import (
    claim_next_task,
    execute_task,
    recover_stale_tasks,
)

if TYPE_CHECKING:
    from core.extensions.contract import AgentProvider
    from core.extensions.context import ExtensionContext

logger = logging.getLogger(__name__)


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
                    self._agent_registry[ext_id] = provider
                    logger.info(
                        "task_engine: registered agent '%s' (ext=%s)", ext_id, ext_id
                    )
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

        if parent_id and self._db:
            await try_resume_parent(self._db, parent_id)

        if not parent_id and status in ("done", "failed"):
            await self._notify_task_completed(task_id, status, payload)

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
        self._agent_registry.clear()

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
        max_steps: int | None = None,
        output_channel: str | None = None,
    ) -> SubmitTaskResult:
        if not self._db or not self._ctx:
            return SubmitTaskResult(
                task_id="", status="error", message="Extension not initialized"
            )

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
            "max_steps": max_steps
            or int(self._ctx.get_config("default_max_steps", 20)),
            "source": "orchestrator",
            "output_channel": output_channel,
        }
        conn = await self._db.ensure_conn()
        now = time.time()
        await conn.execute(
            """
            INSERT INTO agent_task (task_id, parent_id, run_id, agent_id, status, priority, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                task_id,
                parent_task_id,
                run_id,
                agent_id,
                priority,
                json_dumps_unicode(payload),
                now,
                now,
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

    async def _cancel_task(self, task_id: str, reason: str = "") -> CancelTaskResult:
        """Cancel a task. Works on pending, running, waiting, and human_review tasks."""
        if not self._db:
            return CancelTaskResult(
                task_id=task_id, status="error", message="Extension not initialized"
            )
        return await query_cancel_task(self._db, task_id, reason)

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
                        lambda a: self._agent_registry.get(a),
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
