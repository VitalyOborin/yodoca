"""Scheduler extension: ToolProvider + ServiceProvider for one-shot and recurring EventBus schedules."""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import aiosqlite
from pydantic import Field
from agents import function_tool
from croniter import croniter

_ONE_SHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS one_shot_schedules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic        TEXT NOT NULL,
    payload      TEXT NOT NULL,
    fire_at      REAL NOT NULL,
    status       TEXT NOT NULL DEFAULT 'scheduled',
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oss_fire_at ON one_shot_schedules(fire_at, status);
CREATE INDEX IF NOT EXISTS idx_oss_status ON one_shot_schedules(status);
"""

_RECURRING_SCHEMA = """
CREATE TABLE IF NOT EXISTS recurring_schedules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic        TEXT NOT NULL,
    payload      TEXT NOT NULL,
    cron_expr    TEXT,
    every_sec    REAL,
    until_at     REAL,
    status       TEXT NOT NULL DEFAULT 'active',
    next_fire_at REAL NOT NULL,
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rs_next_fire ON recurring_schedules(next_fire_at, status);
"""


class _SchedulerStore:
    """SQLite-backed store for one-shot and recurring schedules."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(self._db_path))
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            cursor = await self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='one_shot_schedules'"
            )
            if await cursor.fetchone():
                info = await self._conn.execute("PRAGMA table_info(one_shot_schedules)")
                if any(row[1] == "deferred_id" for row in await info.fetchall()):
                    await self._conn.execute("DROP TABLE one_shot_schedules")
            await self._conn.executescript(_ONE_SHOT_SCHEMA)
            await self._conn.executescript(_RECURRING_SCHEMA)
            await self._conn.commit()
        return self._conn

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def insert_one_shot(
        self, topic: str, payload: str, fire_at: float
    ) -> int:
        conn = await self._ensure_conn()
        now = time.time()
        cursor = await conn.execute(
            """
            INSERT INTO one_shot_schedules (topic, payload, fire_at, status, created_at)
            VALUES (?, ?, ?, 'scheduled', ?)
            """,
            (topic, payload, fire_at, now),
        )
        await conn.commit()
        return cursor.lastrowid or 0

    async def fetch_due_one_shot(self, now: float) -> list[dict[str, Any]]:
        """Fetch one-shot schedules due to fire (status=scheduled, fire_at <= now)."""
        conn = await self._ensure_conn()
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """
            SELECT id, topic, payload, fire_at
            FROM one_shot_schedules
            WHERE status = 'scheduled' AND fire_at <= ?
            ORDER BY fire_at
            """,
            (now,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def mark_one_shot_fired(self, row_id: int) -> None:
        """Mark one-shot schedule as fired."""
        conn = await self._ensure_conn()
        await conn.execute(
            "UPDATE one_shot_schedules SET status = 'fired' WHERE id = ?",
            (row_id,),
        )
        await conn.commit()

    async def insert_recurring(
        self,
        topic: str,
        payload: str,
        cron_expr: str | None,
        every_sec: float | None,
        until_at: float | None,
        next_fire_at: float,
    ) -> int:
        conn = await self._ensure_conn()
        now = time.time()
        cursor = await conn.execute(
            """
            INSERT INTO recurring_schedules (topic, payload, cron_expr, every_sec, until_at, status, next_fire_at, created_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (topic, payload, cron_expr, every_sec, until_at, next_fire_at, now),
        )
        await conn.commit()
        return cursor.lastrowid or 0

    async def fetch_due_recurring(self, now: float) -> list[dict[str, Any]]:
        conn = await self._ensure_conn()
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """
            SELECT id, topic, payload, cron_expr, every_sec, until_at
            FROM recurring_schedules
            WHERE status = 'active' AND next_fire_at <= ?
            ORDER BY next_fire_at
            """,
            (now,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def advance_next(self, row_id: int, now: float) -> None:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT cron_expr, every_sec, until_at FROM recurring_schedules WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return
        cron_expr, every_sec, until_at = row
        if until_at is not None and until_at < now:
            await conn.execute(
                "UPDATE recurring_schedules SET status = 'cancelled' WHERE id = ?",
                (row_id,),
            )
            await conn.commit()
            return
        if cron_expr:
            c = croniter(cron_expr, now)
            next_fire = c.get_next(float)
        else:
            next_fire = now + (every_sec or 0)
        await conn.execute(
            "UPDATE recurring_schedules SET next_fire_at = ? WHERE id = ?",
            (next_fire, row_id),
        )
        await conn.commit()

    async def recover_recurring(self, now: float) -> None:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            """
            SELECT id, cron_expr, every_sec, until_at, next_fire_at
            FROM recurring_schedules
            WHERE status = 'active' AND next_fire_at < ?
            """,
            (now,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            row_id, cron_expr, every_sec, until_at, _ = row
            if until_at is not None and until_at < now:
                await conn.execute(
                    "UPDATE recurring_schedules SET status = 'cancelled' WHERE id = ?",
                    (row_id,),
                )
                continue
            if cron_expr:
                c = croniter(cron_expr, now)
                next_fire = c.get_next(float)
            else:
                next_fire = now + (every_sec or 0)
            await conn.execute(
                "UPDATE recurring_schedules SET next_fire_at = ? WHERE id = ?",
                (next_fire, row_id),
            )
        await conn.commit()

    async def list_all(
        self, status_filter: str | None = None
    ) -> list[dict[str, Any]]:
        conn = await self._ensure_conn()
        conn.row_factory = aiosqlite.Row
        result: list[dict[str, Any]] = []

        oss_sql = "SELECT id, topic, payload, fire_at, status, created_at FROM one_shot_schedules"
        oss_params: tuple = ()
        if status_filter:
            oss_sql += " WHERE status = ?"
            oss_params = (status_filter,)
        oss_sql += " ORDER BY created_at DESC"
        cursor = await conn.execute(oss_sql, oss_params)
        for row in await cursor.fetchall():
            r = dict(row)
            r["type"] = "one_shot"
            r["fire_at_or_next"] = r.pop("fire_at")
            result.append(r)

        rs_sql = "SELECT id, topic, payload, cron_expr, every_sec, until_at, status, next_fire_at, created_at FROM recurring_schedules"
        rs_params: tuple = ()
        if status_filter:
            rs_sql += " WHERE status = ?"
            rs_params = (status_filter,)
        rs_sql += " ORDER BY created_at DESC"
        cursor = await conn.execute(rs_sql, rs_params)
        for row in await cursor.fetchall():
            r = dict(row)
            r["type"] = "recurring"
            r["fire_at_or_next"] = r.pop("next_fire_at")
            result.append(r)

        return result

    async def cancel_one_shot(self, row_id: int) -> bool:
        """Mark one-shot as cancelled. Returns True if found and was scheduled."""
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "UPDATE one_shot_schedules SET status = 'cancelled' WHERE id = ? AND status = 'scheduled'",
            (row_id,),
        )
        await conn.commit()
        return cursor.rowcount > 0

    async def cancel_recurring(self, row_id: int) -> None:
        conn = await self._ensure_conn()
        await conn.execute(
            "UPDATE recurring_schedules SET status = 'cancelled' WHERE id = ?",
            (row_id,),
        )
        await conn.commit()

    async def update_recurring(
        self,
        row_id: int,
        cron_expr: str | None = None,
        every_sec: float | None = None,
        until_at: float | None = None,
        status: str | None = None,
    ) -> float | None:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "SELECT cron_expr, every_sec, until_at, status FROM recurring_schedules WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        old_cron, old_every, old_until, old_status = row
        if old_status == "cancelled":
            return None

        updates: list[str] = []
        params: list[Any] = []
        if cron_expr is not None:
            updates.append("cron_expr = ?")
            params.append(cron_expr)
        if every_sec is not None:
            updates.append("every_sec = ?")
            params.append(every_sec)
        if until_at is not None:
            updates.append("until_at = ?")
            params.append(until_at)
        if status is not None:
            updates.append("status = ?")
            params.append(status)

        expr_changed = cron_expr is not None or every_sec is not None or until_at is not None
        if not expr_changed and not updates:
            cursor = await conn.execute(
                "SELECT next_fire_at FROM recurring_schedules WHERE id = ?",
                (row_id,),
            )
            r = await cursor.fetchone()
            return r[0] if r else None

        next_fire: float | None = None
        if expr_changed:
            new_cron = cron_expr if cron_expr is not None else old_cron
            new_every = every_sec if every_sec is not None else old_every
            new_until = until_at if until_at is not None else old_until
            now = time.time()
            if new_cron:
                c = croniter(new_cron, now)
                next_fire = c.get_next(float)
            else:
                next_fire = now + (new_every or 0)
            if new_until is not None and next_fire > new_until:
                next_fire = new_until
            updates.append("next_fire_at = ?")
            params.append(next_fire)

        if next_fire is None:
            cursor = await conn.execute(
                "SELECT next_fire_at FROM recurring_schedules WHERE id = ?",
                (row_id,),
            )
            r = await cursor.fetchone()
            next_fire = r[0] if r else 0
        params.append(row_id)
        await conn.execute(
            f"UPDATE recurring_schedules SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await conn.commit()
        return next_fire


class SchedulerExtension:
    """Extension + ToolProvider + ServiceProvider: schedule one-shot and recurring EventBus events."""

    def __init__(self) -> None:
        self._ctx: Any = None
        self._store: _SchedulerStore | None = None
        self._tick_interval: float = 30.0

    async def initialize(self, context: Any) -> None:
        self._ctx = context
        db_path = context.data_dir / "scheduler.db"
        self._store = _SchedulerStore(db_path)
        await self._store._ensure_conn()
        self._tick_interval = float(context.get_config("tick_interval", 30))

    async def start(self) -> None:
        if self._store:
            now = time.time()
            await self._store.recover_recurring(now)
            due = await self._store.fetch_due_one_shot(now)
            for row in due:
                payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
                await self._ctx.emit(row["topic"], payload)
                await self._store.mark_one_shot_fired(row["id"])

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        if self._store:
            await self._store.close()
            self._store = None

    def health_check(self) -> bool:
        return self._store is not None

    def get_tools(self) -> list[Any]:
        if not self._store:
            return []
        store = self._store
        ctx = self._ctx

        @function_tool(name_override="schedule_once")
        async def schedule_once(
            topic: str,
            payload_json: str,
            delay_seconds: Annotated[float | None, Field(default=None, gt=0)] = None,
            at_iso: str | None = None,
        ) -> str:
            """Schedule a one-shot event to fire once.

            Provide exactly one of: delay_seconds (seconds from now) or at_iso (ISO 8601 datetime).

            Payload contracts for system topics:
            - system.user.notify: use key "text" for static messages known at scheduling time.
              Example: payload_json='{"text": "<ready message>", "channel_id": null}'
            - system.agent.task: use key "prompt" for dynamic content, decisions, anything requiring reasoning at fire time.
              Example: payload_json='{"prompt": "Tell the user current time in HH:MM", "channel_id": null}'
            - system.agent.background: use key "prompt" for maintenance, analysis; no user response needed.
              Example: payload_json='{"prompt": "<quiet task>"}'

            Args:
                topic: Event topic (e.g. system.user.notify, system.agent.task).
                payload_json: JSON string payload. For system.user.notify use {"text": "..."}.
                delay_seconds: Seconds from now until fire. Mutually exclusive with at_iso.
                at_iso: ISO 8601 datetime (e.g. 2025-02-21T10:00:00). Mutually exclusive with delay_seconds.
            """
            if (delay_seconds is None) == (at_iso is None):
                return "Error: provide exactly one of delay_seconds or at_iso."
            if delay_seconds is not None and delay_seconds <= 0:
                return "Error: delay_seconds must be positive."
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError as e:
                return f"Error: invalid payload_json: {e}"
            if at_iso:
                try:
                    dt = datetime.fromisoformat(at_iso.replace("Z", "+00:00"))
                    fire_at = dt.timestamp()
                except (ValueError, TypeError):
                    return "Error: invalid at_iso format. Use ISO 8601 (e.g. 2025-02-21T10:00:00)."
                if fire_at <= time.time():
                    return "Error: at_iso must be in the future."
                delay = fire_at - time.time()
            else:
                delay = delay_seconds
            fire_at = time.time() + delay
            row_id = await store.insert_one_shot(topic, payload_json, fire_at)
            return f"Scheduled one-shot #{row_id}: topic={topic}, fires in {int(delay)}s."

        @function_tool(name_override="schedule_recurring")
        async def schedule_recurring(
            topic: str,
            payload_json: str,
            cron: str | None = None,
            every_seconds: float | None = None,
            until_iso: str | None = None,
        ) -> str:
            """Create a recurring schedule.

            Provide exactly one of: cron (e.g. '0 9 * * *') or every_seconds. Optionally set until_iso for end datetime.

            Payload contracts for system topics:
            - system.user.notify: use key "text" for static messages known at scheduling time.
              Example: payload_json='{"text": "<ready message>", "channel_id": null}'
            - system.agent.task: use key "prompt" for dynamic content, decisions, anything requiring reasoning at fire time.
              Example: payload_json='{"prompt": "Tell the user current time in HH:MM", "channel_id": null}'
            - system.agent.background: use key "prompt" for maintenance, analysis; no user response needed.
              Example: payload_json='{"prompt": "<quiet task>"}'

            Args:
                topic: Event topic (e.g. system.user.notify, system.agent.task).
                payload_json: JSON string payload. For system.user.notify use {"text": "..."}.
                cron: Cron expression (e.g. '0 9 * * *' for daily at 9:00). Mutually exclusive with every_seconds.
                every_seconds: Interval in seconds. Mutually exclusive with cron.
                until_iso: Optional ISO 8601 end datetime. Schedule stops after this time.
            """
            if (cron is None or not cron.strip()) == (every_seconds is None or every_seconds <= 0):
                return "Error: provide exactly one of cron or every_seconds (positive)."
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError as e:
                return f"Error: invalid payload_json: {e}"
            if cron:
                try:
                    c = croniter(cron.strip(), time.time())
                    next_fire = c.get_next(float)
                except (ValueError, KeyError) as e:
                    return f"Error: invalid cron expression: {e}"
            else:
                next_fire = time.time() + every_seconds
            until_at: float | None = None
            if until_iso:
                try:
                    dt = datetime.fromisoformat(until_iso.replace("Z", "+00:00"))
                    until_at = dt.timestamp()
                except (ValueError, TypeError):
                    return "Error: invalid until_iso format."
                if until_at <= time.time():
                    return "Error: until_iso must be in the future."
            row_id = await store.insert_recurring(
                topic,
                payload_json,
                cron.strip() if cron else None,
                every_seconds if every_seconds else None,
                until_at,
                next_fire,
            )
            iso = datetime.fromtimestamp(next_fire).isoformat()
            return f"Recurring schedule #{row_id} created. Next fire: {iso}."

        @function_tool(name_override="list_schedules")
        async def list_schedules(
            status: Literal["scheduled", "fired", "cancelled", "active", "paused"] | None = None,
        ) -> str:
            """List all schedules. Returns JSON array.

            Args:
                status: Optional filter. For one-shot: scheduled, fired, cancelled.
                    For recurring: active, paused, cancelled.
            """
            rows = await store.list_all(status)
            if not rows:
                return "[]"
            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "type": r["type"],
                    "topic": r["topic"],
                    "payload": json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
                    "next_fire_iso": datetime.fromtimestamp(r["fire_at_or_next"]).isoformat(),
                    "status": r["status"],
                })
            return json.dumps(result, ensure_ascii=False)

        @function_tool(name_override="cancel_schedule")
        async def cancel_schedule(
            schedule_id: int,
            schedule_type: Literal["one_shot", "recurring"] = "one_shot",
        ) -> str:
            """Cancel a schedule.

            Args:
                schedule_id: ID of the schedule to cancel (from list_schedules).
                schedule_type: one_shot or recurring.
            """
            if schedule_type == "one_shot":
                ok = await store.cancel_one_shot(schedule_id)
                if not ok:
                    return f"Schedule #{schedule_id} not found or already fired/cancelled."
            else:
                await store.cancel_recurring(schedule_id)
            return f"Schedule #{schedule_id} cancelled."

        @function_tool(name_override="update_recurring_schedule")
        async def update_recurring_schedule(
            schedule_id: int,
            cron: str | None = None,
            every_seconds: float | None = None,
            until_iso: str | None = None,
            status: Literal["active", "paused"] | None = None,
        ) -> str:
            """Update a recurring schedule. Does not apply to one-shot schedules.

            Args:
                schedule_id: ID of the recurring schedule (from list_schedules).
                cron: New cron expression. Mutually exclusive with every_seconds.
                every_seconds: New interval in seconds. Mutually exclusive with cron.
                until_iso: New ISO 8601 end datetime.
                status: active or paused.
            """
            until_at: float | None = None
            if until_iso:
                try:
                    dt = datetime.fromisoformat(until_iso.replace("Z", "+00:00"))
                    until_at = dt.timestamp()
                except (ValueError, TypeError):
                    return "Error: invalid until_iso format."
            next_fire = await store.update_recurring(
                schedule_id,
                cron=cron.strip() if cron else None,
                every_sec=every_seconds,
                until_at=until_at,
                status=status,
            )
            if next_fire is None:
                return f"Schedule #{schedule_id} not found or cancelled."
            iso = datetime.fromtimestamp(next_fire).isoformat()
            return f"Schedule #{schedule_id} updated. Next fire: {iso}."

        return [
            schedule_once,
            schedule_recurring,
            list_schedules,
            cancel_schedule,
            update_recurring_schedule,
        ]

    async def run_background(self) -> None:
        store = self._store
        ctx = self._ctx
        if not store or not ctx:
            return
        while True:
            try:
                await asyncio.sleep(self._tick_interval)
                now = time.time()
                due_one_shot = await store.fetch_due_one_shot(now)
                for row in due_one_shot:
                    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
                    await ctx.emit(row["topic"], payload)
                    await store.mark_one_shot_fired(row["id"])
                due_recurring = await store.fetch_due_recurring(now)
                for row in due_recurring:
                    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
                    await ctx.emit(row["topic"], payload)
                    await store.advance_next(row["id"], now)
            except asyncio.CancelledError:
                break
