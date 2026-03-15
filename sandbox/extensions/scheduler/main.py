"""Scheduler extension: ToolProvider + ServiceProvider for one-shot and recurring EventBus schedules."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from agents import function_tool
from croniter import croniter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _to_utc_iso(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

# --- Tool result models (structured output per agent_tools skill) ---


class ScheduleOnceResult(BaseModel):
    """Result of schedule_once tool."""

    success: bool
    schedule_id: int = 0
    topic: str = ""
    fires_in_seconds: int = 0
    status: str = "scheduled"
    error: str | None = None


class ScheduleRecurringResult(BaseModel):
    """Result of schedule_recurring tool."""

    success: bool
    schedule_id: int = 0
    next_fire_iso: str = ""
    status: str = "created"
    error: str | None = None


class ScheduleItem(BaseModel):
    """Single schedule entry in list_schedules."""

    id: int
    type: Literal["one_shot", "recurring"]
    topic: str
    payload: dict[str, Any] | str
    next_fire_iso: str
    status: str


class ListSchedulesResult(BaseModel):
    """Result of list_schedules tool."""

    success: bool
    schedules: list[ScheduleItem] = Field(default_factory=list)
    count: int = 0
    error: str | None = None


class CancelScheduleResult(BaseModel):
    """Result of cancel_schedule tool."""

    success: bool
    schedule_id: int
    message: str
    error: str | None = None


class UpdateRecurringResult(BaseModel):
    """Result of update_recurring_schedule tool."""

    success: bool
    schedule_id: int = 0
    next_fire_iso: str = ""
    message: str = ""
    error: str | None = None


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


def _compute_next_fire(
    cron_expr: str | None, every_sec: float | None, now: float
) -> float:
    """Calculate next fire time from cron expression or interval."""
    if cron_expr:
        return croniter(cron_expr, now).get_next(float)
    return now + (every_sec or 0)


class _SchedulerStore:
    """SQLite-backed store for one-shot and recurring schedules."""

    def __init__(
        self,
        db_path: Path,
        on_cancel: Any | None = None,
    ) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._on_cancel = on_cancel

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

    async def insert_one_shot(self, topic: str, payload: str, fire_at: float) -> int:
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
        next_fire = _compute_next_fire(cron_expr, every_sec, now)
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
            next_fire = _compute_next_fire(cron_expr, every_sec, now)
            await conn.execute(
                "UPDATE recurring_schedules SET next_fire_at = ? WHERE id = ?",
                (next_fire, row_id),
            )
        await conn.commit()

    async def list_all(self, status_filter: str | None = None) -> list[dict[str, Any]]:
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
        cancelled = cursor.rowcount > 0
        if cancelled and self._on_cancel:
            await self._on_cancel(row_id, "one_shot")
        return cancelled

    async def cancel_recurring(self, row_id: int) -> None:
        conn = await self._ensure_conn()
        cursor = await conn.execute(
            "UPDATE recurring_schedules SET status = 'cancelled' WHERE id = ?",
            (row_id,),
        )
        await conn.commit()
        if cursor.rowcount > 0 and self._on_cancel:
            await self._on_cancel(row_id, "recurring")

    async def update_recurring(
        self,
        row_id: int,
        cron_expr: str | None = None,
        every_sec: float | None = None,
        until_at: float | None = None,
        status: str | None = None,
        set_until: bool = False,
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
        if set_until:
            updates.append("until_at = ?")
            params.append(until_at)
        if status is not None:
            updates.append("status = ?")
            params.append(status)

        expr_changed = cron_expr is not None or every_sec is not None or set_until
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
            new_until = until_at if set_until else old_until
            now = time.time()
            next_fire = _compute_next_fire(new_cron, new_every, now)
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


def _build_event_payload(
    topic: str,
    message: str,
    channel_id: str | None,
    payload_extra: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build event payload from tool args. Uses payload_extra for custom topics."""
    if topic == "system.user.notify":
        payload: dict[str, Any] = {"text": message}
    elif topic in ("system.agent.task", "system.agent.background"):
        payload = {"prompt": message}
    elif payload_extra:
        payload = dict(payload_extra)
        if (
            "message" not in payload
            and "text" not in payload
            and "prompt" not in payload
        ):
            payload["message"] = message
    else:
        payload = {"message": message}
    if channel_id:
        payload["channel_id"] = channel_id
    return payload


def _parse_iso(value: str) -> float | None:
    """Parse ISO 8601 datetime string to timestamp. Returns None on invalid format."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _parse_payload_json(raw: Any) -> Any:
    """Parse JSON string payload to dict, pass through if already parsed."""
    return json.loads(raw) if isinstance(raw, str) else raw


def _with_schedule_metadata(
    payload: Any,
    schedule_id: int,
    schedule_type: Literal["one_shot", "recurring"],
) -> dict[str, Any]:
    """Attach internal schedule marker for queue purge on cancel."""
    enriched = dict(payload) if isinstance(payload, dict) else {"message": payload}
    enriched["__schedule"] = {"id": schedule_id, "type": schedule_type}
    return enriched


class SchedulerExtension:
    """Extension + ToolProvider + ServiceProvider: schedule one-shot and recurring EventBus events."""

    def __init__(self) -> None:
        self._ctx: Any = None
        self._store: _SchedulerStore | None = None
        self._tick_interval: float = 30.0

    async def initialize(self, context: Any) -> None:
        self._ctx = context
        db_path = context.data_dir / "scheduler.db"
        self._store = _SchedulerStore(db_path, on_cancel=self._on_store_cancel)
        await self._store._ensure_conn()
        self._tick_interval = float(context.get_config("tick_interval", 30))

    async def _on_store_cancel(
        self,
        schedule_id: int,
        schedule_type: Literal["one_shot", "recurring"],
    ) -> None:
        if not self._ctx:
            return
        try:
            purge_fn = getattr(self._ctx, "purge_scheduled_events", None)
            if not callable(purge_fn):
                return
            result = purge_fn(schedule_id, schedule_type)
            deleted = await result if asyncio.iscoroutine(result) else 0
            logger.info(
                "scheduler cancel purge: %s#%s deleted=%s",
                schedule_type,
                schedule_id,
                deleted,
            )
        except Exception as e:
            logger.warning(
                "scheduler cancel purge failed for %s#%s: %s",
                schedule_type,
                schedule_id,
                e,
            )

    async def start(self) -> None:
        if self._store:
            now = time.time()
            await self._store.recover_recurring(now)
            due = await self._store.fetch_due_one_shot(now)
            for row in due:
                payload = _with_schedule_metadata(
                    _parse_payload_json(row["payload"]),
                    row["id"],
                    "one_shot",
                )
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

        @function_tool(name_override="schedule_once", strict_mode=False)
        async def schedule_once(
            topic: str = Field(
                ...,
                description=(
                    "Event topic. Use 'system.user.notify' to send a message to the user, "
                    "'system.agent.task' to delegate reasoning to an agent at fire time, "
                    "'system.agent.background' for maintenance tasks without user response."
                ),
            ),
            message: str = Field(
                ...,
                description=(
                    "Text message (for system.user.notify) or prompt instruction "
                    "(for system.agent.task / system.agent.background)."
                ),
            ),
            channel_id: str | None = None,
            payload_extra: dict[str, Any] | None = Field(
                default=None,
                description="Optional extra payload fields for custom (non-system) topics only.",
            ),
            delay_seconds: int | None = None,
            at_iso: str | None = None,
        ) -> ScheduleOnceResult:
            """Schedule a one-shot event to fire once.

            Provide exactly one of: delay_seconds (seconds from now) or at_iso (ISO 8601 datetime).

            Args:
                channel_id: Optional delivery channel ID (e.g. 'telegram_channel'). If null, uses the default channel.
                payload_extra: Optional dict of extra payload fields for custom topics only.
                delay_seconds: Seconds from now until fire (positive number). Mutually exclusive with at_iso.
                at_iso: ISO 8601 datetime (e.g. '2025-02-21T10:00:00'). Mutually exclusive with delay_seconds.
            """
            if (delay_seconds is None) == (at_iso is None):
                return ScheduleOnceResult(
                    success=False,
                    status="error",
                    error="provide exactly one of delay_seconds or at_iso.",
                )
            if delay_seconds is not None and delay_seconds <= 0:
                return ScheduleOnceResult(
                    success=False,
                    status="error",
                    error="delay_seconds must be positive.",
                )

            payload = _build_event_payload(topic, message, channel_id, payload_extra)

            if at_iso:
                fire_at = _parse_iso(at_iso)
                if fire_at is None:
                    return ScheduleOnceResult(
                        success=False,
                        status="error",
                        error="invalid at_iso format. Use ISO 8601 (e.g. '2025-02-21T10:00:00').",
                    )
                if fire_at <= time.time():
                    return ScheduleOnceResult(
                        success=False,
                        status="error",
                        error="at_iso must be in the future.",
                    )
            else:
                fire_at = time.time() + delay_seconds  # type: ignore[operator]

            row_id = await store.insert_one_shot(
                topic, json.dumps(payload, ensure_ascii=False), fire_at
            )
            return ScheduleOnceResult(
                success=True,
                schedule_id=row_id,
                topic=topic,
                fires_in_seconds=int(fire_at - time.time()),
                status="scheduled",
            )

        @function_tool(name_override="schedule_recurring", strict_mode=False)
        async def schedule_recurring(
            topic: str = Field(
                ...,
                description=(
                    "Event topic. Use 'system.user.notify' to send a message to the user, "
                    "'system.agent.task' to delegate reasoning to an agent at fire time, "
                    "'system.agent.background' for maintenance tasks without user response."
                ),
            ),
            message: str = Field(
                ...,
                description=(
                    "Text message (for system.user.notify) or prompt instruction "
                    "(for system.agent.task / system.agent.background)."
                ),
            ),
            channel_id: str | None = None,
            payload_extra: dict[str, Any] | None = Field(
                default=None,
                description="Optional extra payload fields for custom (non-system) topics only.",
            ),
            cron: str | None = None,
            every_seconds: float | None = None,
            until_iso: str | None = None,
        ) -> ScheduleRecurringResult:
            """Create a recurring schedule.

            Provide exactly one of: cron (e.g. '0 9 * * *') or every_seconds. Optionally set until_iso for end datetime.

            Args:
                channel_id: Optional delivery channel ID (e.g. 'telegram_channel'). If null, uses the default channel.
                payload_extra: Optional dict of extra payload fields for custom topics only.
                cron: Cron expression (e.g. '0 9 * * *' for daily at 9:00). Mutually exclusive with every_seconds.
                every_seconds: Interval in seconds (positive number). Mutually exclusive with cron.
                until_iso: Optional ISO 8601 end datetime. Schedule stops after this time.
            """
            has_cron = cron is not None and cron.strip()
            has_interval = every_seconds is not None and every_seconds > 0
            if has_cron == has_interval:
                return ScheduleRecurringResult(
                    success=False,
                    status="error",
                    error="provide exactly one of cron or every_seconds (positive).",
                )

            payload = _build_event_payload(topic, message, channel_id, payload_extra)

            if cron:
                try:
                    next_fire = croniter(cron.strip(), time.time()).get_next(float)
                except (ValueError, KeyError) as e:
                    return ScheduleRecurringResult(
                        success=False,
                        status="error",
                        error=f"invalid cron expression: {e}",
                    )
            else:
                next_fire = time.time() + every_seconds  # type: ignore[operator]

            until_at: float | None = None
            if until_iso:
                until_at = _parse_iso(until_iso)
                if until_at is None:
                    return ScheduleRecurringResult(
                        success=False, status="error", error="invalid until_iso format."
                    )
                if until_at <= time.time():
                    return ScheduleRecurringResult(
                        success=False,
                        status="error",
                        error="until_iso must be in the future.",
                    )

            row_id = await store.insert_recurring(
                topic,
                json.dumps(payload, ensure_ascii=False),
                cron.strip() if cron else None,
                every_seconds if every_seconds else None,
                until_at,
                next_fire,
            )
            iso = _to_utc_iso(next_fire)
            return ScheduleRecurringResult(
                success=True,
                schedule_id=row_id,
                next_fire_iso=iso,
                status="created",
            )

        @function_tool(name_override="list_schedules")
        async def list_schedules(
            status: Literal["scheduled", "fired", "cancelled", "active", "paused"]
            | None = None,
        ) -> ListSchedulesResult:
            """List all schedules.

            Args:
                status: Optional filter. For one-shot: scheduled, fired, cancelled.
                    For recurring: active, paused, cancelled.
            """
            rows = await store.list_all(status)
            if not rows:
                return ListSchedulesResult(success=True, schedules=[], count=0)
            schedules = [
                ScheduleItem(
                    id=r["id"],
                    type=r["type"],
                    topic=r["topic"],
                    payload=_parse_payload_json(r["payload"]),
                    next_fire_iso=_to_utc_iso(r["fire_at_or_next"]),
                    status=r["status"],
                )
                for r in rows
            ]
            return ListSchedulesResult(
                success=True, schedules=schedules, count=len(schedules)
            )

        @function_tool(name_override="cancel_schedule")
        async def cancel_schedule(
            schedule_id: int,
            schedule_type: Literal["one_shot", "recurring"] = "one_shot",
        ) -> CancelScheduleResult:
            """Cancel a schedule.

            Args:
                schedule_id: ID of the schedule to cancel (from list_schedules).
                schedule_type: one_shot or recurring.
            """
            if schedule_type == "one_shot":
                ok = await store.cancel_one_shot(schedule_id)
                if not ok:
                    return CancelScheduleResult(
                        success=False,
                        schedule_id=schedule_id,
                        message="",
                        error="Schedule not found or already fired/cancelled.",
                    )
            else:
                await store.cancel_recurring(schedule_id)
            return CancelScheduleResult(
                success=True,
                schedule_id=schedule_id,
                message=f"Schedule #{schedule_id} cancelled.",
            )

        @function_tool(name_override="update_recurring_schedule", strict_mode=False)
        async def update_recurring_schedule(
            schedule_id: int,
            cron: str | None = None,
            every_seconds: float | None = None,
            until_iso: str | None = None,
            status: Literal["active", "paused"] | None = None,
        ) -> UpdateRecurringResult:
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
                until_at = _parse_iso(until_iso)
                if until_at is None:
                    return UpdateRecurringResult(
                        success=False, message="", error="invalid until_iso format."
                    )
            next_fire = await store.update_recurring(
                schedule_id,
                cron_expr=cron.strip() if cron else None,
                every_sec=every_seconds,
                until_at=until_at,
                status=status,
            )
            if next_fire is None:
                return UpdateRecurringResult(
                    success=False,
                    schedule_id=schedule_id,
                    message="",
                    error="Schedule not found or cancelled.",
                )
            iso = _to_utc_iso(next_fire)
            return UpdateRecurringResult(
                success=True,
                schedule_id=schedule_id,
                next_fire_iso=iso,
                message=f"Schedule #{schedule_id} updated. Next fire: {iso}.",
            )

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
                    payload = _with_schedule_metadata(
                        _parse_payload_json(row["payload"]),
                        row["id"],
                        "one_shot",
                    )
                    await ctx.emit(row["topic"], payload)
                    await store.mark_one_shot_fired(row["id"])
                due_recurring = await store.fetch_due_recurring(now)
                for row in due_recurring:
                    payload = _with_schedule_metadata(
                        _parse_payload_json(row["payload"]),
                        row["id"],
                        "recurring",
                    )
                    await ctx.emit(row["topic"], payload)
                    await store.advance_next(row["id"], now)
            except asyncio.CancelledError:
                break
