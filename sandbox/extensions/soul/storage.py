"""SQLite storage for the soul runtime."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sandbox.extensions.soul.models import CompanionState

_INCREMENTABLE_METRICS = {
    "outreach_attempts",
    "outreach_responses",
    "outreach_ignored",
    "outreach_timing_miss",
    "outreach_rejected",
    "message_count",
    "inference_count",
    "perception_corrections",
}

_REPLACEABLE_METRICS = {
    "phase_distribution_json",
    "openness_avg",
}

_ALLOWED_METRICS = _INCREMENTABLE_METRICS | _REPLACEABLE_METRICS


class SoulStorage:
    """Thin async wrapper around soul.db."""

    def __init__(self, db_path: Path, schema_path: Path) -> None:
        self._db_path = db_path
        self._schema_path = schema_path
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        schema = self._schema_path.read_text(encoding="utf-8")
        await asyncio.to_thread(self._init_connection, schema)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SoulStorage is not initialized")
        return self._conn

    def _init_connection(self, schema: str) -> None:
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(schema)
        self._conn.commit()

    async def load_state(self) -> CompanionState | None:
        async with self._lock:
            return await asyncio.to_thread(self._load_state_sync)

    def _load_state_sync(self) -> CompanionState | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT state_json FROM soul_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return CompanionState.from_json(str(row["state_json"]))

    async def save_state(
        self,
        state: CompanionState,
        *,
        updated_at: datetime | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_state_sync, state, updated_at)

    def _save_state_sync(
        self,
        state: CompanionState,
        updated_at: datetime | None,
    ) -> None:
        ts = (updated_at or datetime.now(UTC)).isoformat()
        payload = state.to_json()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO soul_state (id, state_json, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (payload, ts),
        )
        conn.commit()

    async def append_trace(
        self,
        *,
        trace_type: str,
        phase: str,
        content: str,
        payload_json: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._append_trace_sync,
                trace_type,
                phase,
                content,
                payload_json,
                created_at,
            )

    def _append_trace_sync(
        self,
        trace_type: str,
        phase: str,
        content: str,
        payload_json: str | None,
        created_at: datetime | None,
    ) -> None:
        ts = (created_at or datetime.now(UTC)).isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO traces (trace_type, phase, content, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (trace_type, phase, content, payload_json, ts),
        )
        conn.commit()

    async def upsert_daily_metrics(
        self,
        metric_date: date,
        **increments: Any,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._upsert_daily_metrics_sync, metric_date, increments
            )

    def _upsert_daily_metrics_sync(
        self,
        metric_date: date,
        increments: dict[str, Any],
    ) -> None:
        metric_key = metric_date.isoformat()
        updated_at = datetime.now(UTC).isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO soul_metrics (date, updated_at)
            VALUES (?, ?)
            ON CONFLICT(date) DO NOTHING
            """,
            (metric_key, updated_at),
        )

        for key, value in increments.items():
            if key not in _ALLOWED_METRICS:
                raise ValueError(f"Unsupported metrics field: {key}")
            if key in _REPLACEABLE_METRICS:
                conn.execute(
                    f"UPDATE soul_metrics SET {key} = ?, updated_at = ? WHERE date = ?",
                    (value, updated_at, metric_key),
                )
            else:
                conn.execute(
                    f"""
                    UPDATE soul_metrics
                    SET {key} = {key} + ?, updated_at = ?
                    WHERE date = ?
                    """,
                    (int(value), updated_at, metric_key),
                )
        conn.commit()

    async def append_interaction(
        self,
        *,
        direction: str,
        channel_id: str | None = None,
        outreach_result: str | None = None,
        response_delay_s: int | None = None,
        created_at: datetime | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._append_interaction_sync,
                direction,
                channel_id,
                outreach_result,
                response_delay_s,
                created_at,
            )

    def _append_interaction_sync(
        self,
        direction: str,
        channel_id: str | None,
        outreach_result: str | None,
        response_delay_s: int | None,
        created_at: datetime | None,
    ) -> None:
        ts = created_at or datetime.now(UTC)
        if direction not in {"inbound", "outbound"}:
            raise ValueError(f"Unsupported interaction direction: {direction}")
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO interaction_log (
                direction,
                channel_id,
                hour,
                day_of_week,
                outreach_result,
                response_delay_s,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                direction,
                channel_id,
                ts.hour,
                ts.weekday(),
                outreach_result,
                response_delay_s,
                ts.isoformat(),
            ),
        )
        conn.commit()

    async def cleanup_traces_older_than(self, cutoff: datetime) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._cleanup_traces_sync, cutoff)

    def _cleanup_traces_sync(self, cutoff: datetime) -> int:
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM traces WHERE created_at < ?",
            (cutoff.astimezone(UTC).isoformat(),),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
