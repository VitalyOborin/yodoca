"""Task Engine cleanup: delete old completed/failed/cancelled tasks."""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


async def cleanup_old_tasks(db: Any, retention_days: int) -> dict[str, Any]:
    """Delete tasks and steps older than retention_days. Returns scheduler result dict."""
    cutoff = time.time() - (retention_days * 86400)
    conn = await db.ensure_conn()
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
