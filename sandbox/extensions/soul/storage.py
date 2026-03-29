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
    "reflection_count",
    "perception_corrections",
}

_REPLACEABLE_METRICS = {
    "phase_distribution_json",
    "openness_avg",
}

_AVERAGE_METRICS = {"context_words_avg"}

_ALLOWED_METRICS = _INCREMENTABLE_METRICS | _REPLACEABLE_METRICS | _AVERAGE_METRICS


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
            elif key in _AVERAGE_METRICS:
                row = conn.execute(
                    """
                    SELECT context_words_avg, context_words_samples
                    FROM soul_metrics
                    WHERE date = ?
                    """,
                    (metric_key,),
                ).fetchone()
                samples = int(row["context_words_samples"] or 0)
                avg_value = float(row["context_words_avg"] or 0.0)
                next_samples = samples + 1
                next_avg = ((avg_value * samples) + float(value)) / next_samples
                conn.execute(
                    """
                    UPDATE soul_metrics
                    SET context_words_avg = ?, context_words_samples = ?, updated_at = ?
                    WHERE date = ?
                    """,
                    (next_avg, next_samples, updated_at, metric_key),
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

    async def get_daily_metrics(self, metric_date: date) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_daily_metrics_sync,
                metric_date,
            )

    def _get_daily_metrics_sync(self, metric_date: date) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM soul_metrics WHERE date = ?",
            (metric_date.isoformat(),),
        ).fetchone()
        return dict(row) if row is not None else None

    async def list_daily_metrics_since(self, since: date) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_daily_metrics_since_sync, since)

    def _list_daily_metrics_since_sync(self, since: date) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT *
            FROM soul_metrics
            WHERE date >= ?
            ORDER BY date ASC
            """,
            (since.isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]

    async def append_interaction(
        self,
        *,
        direction: str,
        channel_id: str | None = None,
        outreach_result: str | None = None,
        message_length: int | None = None,
        openness_signal: float | None = None,
        response_delay_s: int | None = None,
        created_at: datetime | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._append_interaction_sync,
                direction,
                channel_id,
                outreach_result,
                message_length,
                openness_signal,
                response_delay_s,
                created_at,
            )

    def _append_interaction_sync(
        self,
        direction: str,
        channel_id: str | None,
        outreach_result: str | None,
        message_length: int | None,
        openness_signal: float | None,
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
                message_length,
                openness_signal,
                response_delay_s,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                direction,
                channel_id,
                ts.hour,
                ts.weekday(),
                outreach_result,
                message_length,
                openness_signal,
                response_delay_s,
                ts.isoformat(),
            ),
        )
        self._update_interaction_pattern_sync(
            hour=ts.hour,
            day_of_week=ts.weekday(),
            direction=direction,
            outreach_result=outreach_result,
            response_delay_s=response_delay_s,
            updated_at=ts,
        )
        conn.commit()

    def _update_interaction_pattern_sync(
        self,
        *,
        hour: int,
        day_of_week: int,
        direction: str,
        outreach_result: str | None,
        response_delay_s: int | None,
        updated_at: datetime,
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO interaction_patterns (
                hour,
                day_of_week,
                updated_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(hour, day_of_week) DO NOTHING
            """,
            (hour, day_of_week, updated_at.isoformat()),
        )
        conn.execute(
            """
            UPDATE interaction_patterns
            SET
                interaction_count = interaction_count + 1,
                inbound_count = inbound_count + ?,
                outbound_count = outbound_count + ?,
                response_count = response_count + ?,
                ignored_count = ignored_count + ?,
                timing_miss_count = timing_miss_count + ?,
                rejected_count = rejected_count + ?,
                updated_at = ?
            WHERE hour = ? AND day_of_week = ?
            """,
            (
                1 if direction == "inbound" else 0,
                1 if direction == "outbound" else 0,
                1 if outreach_result == "response" else 0,
                1 if outreach_result == "ignored" else 0,
                1 if outreach_result == "timing_miss" else 0,
                1 if outreach_result == "rejected" else 0,
                updated_at.isoformat(),
                hour,
                day_of_week,
            ),
        )
        if response_delay_s is not None:
            row = conn.execute(
                """
                SELECT avg_response_delay_s, response_delay_samples
                FROM interaction_patterns
                WHERE hour = ? AND day_of_week = ?
                """,
                (hour, day_of_week),
            ).fetchone()
            samples = int(row["response_delay_samples"] or 0)
            avg_delay = float(row["avg_response_delay_s"] or 0.0)
            next_samples = samples + 1
            next_avg = ((avg_delay * samples) + response_delay_s) / next_samples
            conn.execute(
                """
                UPDATE interaction_patterns
                SET avg_response_delay_s = ?, response_delay_samples = ?
                WHERE hour = ? AND day_of_week = ?
                """,
                (next_avg, next_samples, hour, day_of_week),
            )

    async def cleanup_traces_older_than(self, cutoff: datetime) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._cleanup_traces_sync, cutoff)

    async def get_presence_summary(
        self,
        *,
        hour: int,
        day_of_week: int,
        since: datetime,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_presence_summary_sync,
                hour,
                day_of_week,
                since,
            )

    def _get_presence_summary_sync(
        self,
        hour: int,
        day_of_week: int,
        since: datetime,
    ) -> dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_interactions,
                SUM(
                    CASE
                        WHEN hour = ? AND day_of_week = ? THEN 1
                        ELSE 0
                    END
                ) AS slot_interactions,
                MAX(created_at) AS last_interaction_at
            FROM interaction_log
            WHERE created_at >= ?
            """,
            (hour, day_of_week, since.astimezone(UTC).isoformat()),
        ).fetchone()
        return {
            "total_interactions": int(row["total_interactions"] or 0),
            "slot_interactions": int(row["slot_interactions"] or 0),
            "last_interaction_at": row["last_interaction_at"],
        }

    async def get_interaction_pattern(
        self,
        *,
        hour: int,
        day_of_week: int,
    ) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_interaction_pattern_sync,
                hour,
                day_of_week,
            )

    def _get_interaction_pattern_sync(
        self,
        hour: int,
        day_of_week: int,
    ) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute(
            """
            SELECT
                hour,
                day_of_week,
                interaction_count,
                inbound_count,
                outbound_count,
                response_count,
                ignored_count,
                timing_miss_count,
                rejected_count,
                avg_response_delay_s,
                response_delay_samples,
                updated_at
            FROM interaction_patterns
            WHERE hour = ? AND day_of_week = ?
            """,
            (hour, day_of_week),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    async def list_interactions_since(self, since: datetime) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_interactions_since_sync, since)

    def _list_interactions_since_sync(self, since: datetime) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT
                direction,
                channel_id,
                hour,
                day_of_week,
                outreach_result,
                message_length,
                openness_signal,
                response_delay_s,
                created_at
            FROM interaction_log
            WHERE created_at >= ?
            ORDER BY created_at ASC
            """,
            (since.astimezone(UTC).isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]

    async def save_relationship_pattern(
        self,
        *,
        pattern_key: str,
        pattern_type: str,
        content: str,
        repetition_count: int,
        confidence: float,
        is_permanent: bool,
        source_json: str | None,
        seen_at: datetime,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_relationship_pattern_sync,
                pattern_key,
                pattern_type,
                content,
                repetition_count,
                confidence,
                is_permanent,
                source_json,
                seen_at,
            )

    def _save_relationship_pattern_sync(
        self,
        pattern_key: str,
        pattern_type: str,
        content: str,
        repetition_count: int,
        confidence: float,
        is_permanent: bool,
        source_json: str | None,
        seen_at: datetime,
    ) -> None:
        ts = seen_at.astimezone(UTC).isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO relationship_patterns (
                pattern_key,
                pattern_type,
                content,
                repetition_count,
                confidence,
                first_seen_at,
                last_seen_at,
                is_permanent,
                source_json,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pattern_key) DO UPDATE SET
                pattern_type = excluded.pattern_type,
                content = excluded.content,
                repetition_count = excluded.repetition_count,
                confidence = excluded.confidence,
                last_seen_at = excluded.last_seen_at,
                is_permanent = excluded.is_permanent,
                source_json = excluded.source_json,
                updated_at = excluded.updated_at
            """,
            (
                pattern_key,
                pattern_type,
                content,
                repetition_count,
                confidence,
                ts,
                ts,
                1 if is_permanent else 0,
                source_json,
                ts,
            ),
        )
        conn.commit()

    async def list_relationship_patterns(
        self,
        *,
        permanent_only: bool = False,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_relationship_patterns_sync,
                permanent_only,
            )

    def _list_relationship_patterns_sync(
        self,
        permanent_only: bool,
    ) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT
                pattern_key,
                pattern_type,
                content,
                repetition_count,
                confidence,
                first_seen_at,
                last_seen_at,
                is_permanent,
                source_json,
                updated_at
            FROM relationship_patterns
            WHERE (? = 0 OR is_permanent = 1)
            ORDER BY confidence DESC, updated_at DESC
            """,
            (1 if permanent_only else 0,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _cleanup_traces_sync(self, cutoff: datetime) -> int:
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM traces WHERE created_at < ?",
            (cutoff.astimezone(UTC).isoformat(),),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
