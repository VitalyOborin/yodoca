"""Task Engine chain logic: unblock successors, cascade failure, chain queries."""

import json
import logging
import time
from typing import Any

from state import json_dumps_unicode

logger = logging.getLogger(__name__)


async def unblock_successors(
    db: Any,
    completed_task_id: str,
    status: str,
    result: Any = None,
) -> None:
    """When a task completes, unblock or cascade to successors.

    If predecessor status is 'done': inject result into successor payload, set pending.
    If predecessor failed or cancelled: cascade failure to all downstream blocked tasks.
    """
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        """
        SELECT task_id, payload FROM agent_task
        WHERE after_task_id = ? AND status = 'blocked'
        """,
        (completed_task_id,),
    )
    rows = await cursor.fetchall()
    cols = [d[0] for d in cursor.description]

    if status == "done":
        # Extract content from result for successor context
        predecessor_content = ""
        if result is not None:
            if isinstance(result, dict):
                predecessor_content = result.get("content", str(result))
            else:
                predecessor_content = str(result)

        for row in rows:
            d = dict(zip(cols, row, strict=True))
            task_id = d["task_id"]
            payload_raw = d["payload"]
            payload = (
                json.loads(payload_raw)
                if isinstance(payload_raw, str)
                else (payload_raw or {})
            )
            payload = dict(payload)
            payload["predecessor_result"] = predecessor_content
            payload["predecessor_task_id"] = completed_task_id

            await conn.execute(
                """
                UPDATE agent_task SET payload = ?, status = 'pending', updated_at = ?
                WHERE task_id = ?
                """,
                (json_dumps_unicode(payload), time.time(), task_id),
            )
            logger.info(
                "task_engine: unblocked successor %s (predecessor %s done)",
                task_id,
                completed_task_id,
            )
    else:
        # Cascade failure/cancelled to all downstream
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            task_id = d["task_id"]
            error_msg = f"Predecessor {completed_task_id} {status}"
            await conn.execute(
                """
                UPDATE agent_task SET status = 'failed', error = ?, updated_at = ?
                WHERE task_id = ? AND status = 'blocked'
                """,
                (error_msg, time.time(), task_id),
            )
            # Recursively cascade to this task's successors
            await unblock_successors(db, task_id, "failed", None)

        if rows:
            logger.info(
                "task_engine: cascaded failure from %s to %d blocked successor(s)",
                completed_task_id,
                len(rows),
            )

    await conn.commit()


async def cancel_chain_downstream(db: Any, task_id: str, reason: str = "") -> int:
    """Cancel all blocked tasks downstream of the given task. Returns count cancelled."""
    conn = await db.ensure_conn()
    error_msg = reason or "Cancelled (predecessor cancelled)"
    total = 0

    # Recursively find and cancel successors
    current_ids = [task_id]
    while current_ids:
        placeholders = ",".join("?" for _ in current_ids)
        cursor = await conn.execute(
            f"""
            SELECT task_id FROM agent_task
            WHERE after_task_id IN ({placeholders}) AND status = 'blocked'
            """,
            (*current_ids,),
        )
        rows = await cursor.fetchall()
        next_ids = [r[0] for r in rows]

        for succ_id in next_ids:
            await conn.execute(
                """
                UPDATE agent_task SET status = 'cancelled', error = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (error_msg, time.time(), succ_id),
            )
            total += 1

        current_ids = next_ids

    if total:
        await conn.commit()
        logger.info(
            "task_engine: cancelled %d downstream task(s) of %s",
            total,
            task_id,
        )
    return total


async def get_chain_tasks(db: Any, chain_id: str) -> list[dict]:
    """Query all tasks in a chain, ordered by chain_order."""
    conn = await db.ensure_conn()
    cursor = await conn.execute(
        """
        SELECT task_id, agent_id, status, payload, chain_order
        FROM agent_task
        WHERE chain_id = ?
        ORDER BY chain_order ASC, created_at ASC
        """,
        (chain_id,),
    )
    rows = await cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    result = []
    for row in rows:
        d = dict(zip(cols, row, strict=True))
        payload = (
            json.loads(d["payload"])
            if isinstance(d["payload"], str)
            else (d["payload"] or {})
        )
        d["goal"] = payload.get("goal", "")
        result.append(d)
    return result
