"""TracingStorage: async SQLite storage for execution trace spans."""

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .models import Span, SpanStatus, SpanType

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS execution_traces (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    correlation_id  TEXT,
    parent_span_id  TEXT,
    span_type       TEXT NOT NULL,
    name            TEXT NOT NULL,
    input_summary   TEXT,
    output_summary  TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    error_message   TEXT,
    started_at      REAL NOT NULL,
    completed_at    REAL,
    duration_ms     INTEGER,
    token_input     INTEGER,
    token_output    INTEGER,
    cost_usd        REAL,
    metadata        TEXT
);
CREATE INDEX IF NOT EXISTS idx_trace_session ON execution_traces(session_id);
CREATE INDEX IF NOT EXISTS idx_trace_correlation ON execution_traces(correlation_id);
CREATE INDEX IF NOT EXISTS idx_trace_parent ON execution_traces(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_trace_started ON execution_traces(started_at);
"""


class TracingStorage:
    """Async SQLite storage for execution trace spans."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("TracingStorage is not initialized")
        return self._conn

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        # Backward-compatible migration for older DBs created before cost tracking.
        try:
            await self._conn.execute(
                "ALTER TABLE execution_traces ADD COLUMN cost_usd REAL"
            )
            await self._conn.commit()
        except Exception:
            pass

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def save_span(self, span: Span) -> None:
        conn = self._ensure_conn()
        await conn.execute(
            """\
            INSERT INTO execution_traces
                (id, session_id, correlation_id, parent_span_id, span_type, name,
                 input_summary, output_summary, status, error_message,
                 started_at, completed_at, duration_ms, token_input, token_output, cost_usd, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                span.id,
                span.session_id,
                span.correlation_id,
                span.parent_span_id,
                span.span_type.value,
                span.name,
                span.input_summary,
                span.output_summary,
                span.status.value,
                span.error_message,
                span.started_at,
                span.completed_at,
                span.duration_ms,
                span.token_input,
                span.token_output,
                span.cost_usd,
                json.dumps(span.metadata, ensure_ascii=False)
                if span.metadata
                else "{}",
            ),
        )
        await conn.commit()

    async def update_span(self, span: Span) -> None:
        conn = self._ensure_conn()
        await conn.execute(
            """\
            UPDATE execution_traces
            SET output_summary = ?, status = ?, error_message = ?,
                completed_at = ?, duration_ms = ?, token_input = ?, token_output = ?,
                cost_usd = ?, metadata = ?
            WHERE id = ?
            """,
            (
                span.output_summary,
                span.status.value,
                span.error_message,
                span.completed_at,
                span.duration_ms,
                span.token_input,
                span.token_output,
                span.cost_usd,
                json.dumps(span.metadata, ensure_ascii=False)
                if span.metadata
                else "{}",
                span.id,
            ),
        )
        await conn.commit()

    async def get_span(self, span_id: str) -> Span | None:
        conn = self._ensure_conn()
        cursor = await conn.execute(
            "SELECT * FROM execution_traces WHERE id = ?", (span_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_span(row)

    async def get_trace_tree(self, session_id: str) -> list[Span]:
        conn = self._ensure_conn()
        cursor = await conn.execute(
            "SELECT * FROM execution_traces WHERE session_id = ? ORDER BY started_at",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_span(r) for r in rows]

    async def get_trace_stats(self, session_id: str | None = None) -> dict:
        conn = self._ensure_conn()
        where = ""
        params: tuple[Any, ...] = ()
        if session_id:
            where = "WHERE session_id = ?"
            params = (session_id,)

        cursor = await conn.execute(
            f"""\
            SELECT
                COUNT(*) as total_spans,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                COUNT(CASE WHEN status = 'error' THEN 1 END) as errors,
                COUNT(CASE WHEN status = 'running' THEN 1 END) as running,
                COALESCE(SUM(token_input), 0) as total_token_input,
                COALESCE(SUM(token_output), 0) as total_token_output,
                COALESCE(SUM(cost_usd), 0.0) as total_cost_usd,
                COALESCE(AVG(duration_ms), 0) as avg_duration_ms
            FROM execution_traces {where}
            """,
            params,
        )
        row = await cursor.fetchone()
        if not row:
            return {}

        tool_cursor = await conn.execute(
            f"""\
            SELECT name, COUNT(*) as call_count
            FROM execution_traces
            {("WHERE session_id = ? AND" if session_id else "WHERE")} span_type = 'tool_call'
            GROUP BY name ORDER BY call_count DESC LIMIT 20
            """,
            params,
        )
        tool_rows = await tool_cursor.fetchall()

        return {
            "total_spans": row["total_spans"],
            "completed": row["completed"],
            "errors": row["errors"],
            "running": row["running"],
            "total_token_input": row["total_token_input"],
            "total_token_output": row["total_token_output"],
            "total_cost_usd": round(row["total_cost_usd"], 8),
            "avg_duration_ms": round(row["avg_duration_ms"], 2),
            "top_tools": [
                {"name": r["name"], "count": r["call_count"]} for r in tool_rows
            ],
        }

    async def get_last_trace(self, session_id: str) -> list[Span]:
        """Return the latest agent invocation trace tree for a session."""
        conn = self._ensure_conn()
        cursor = await conn.execute(
            """
            SELECT id FROM execution_traces
            WHERE session_id = ? AND span_type = 'agent_invoke'
            ORDER BY started_at DESC LIMIT 1
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return []
        root_id = row["id"]
        cursor = await conn.execute(
            """
            SELECT * FROM execution_traces
            WHERE session_id = ? AND (id = ? OR parent_span_id = ?)
            ORDER BY started_at
            """,
            (session_id, root_id, root_id),
        )
        rows = await cursor.fetchall()
        return [self._row_to_span(r) for r in rows]

    async def get_session_stats(self, session_id: str) -> dict:
        """Session-level aggregated stats for tracing_get_session_stats."""
        stats = await self.get_trace_stats(session_id=session_id)
        tool_usage = await self.get_tool_usage(session_id=session_id)
        return {
            "session_id": session_id,
            "turns": stats.get("total_spans", 0),
            "tokens_in": stats.get("total_token_input", 0),
            "tokens_out": stats.get("total_token_output", 0),
            "cost_usd": stats.get("total_cost_usd", 0.0),
            "top_tools": tool_usage[:10],
        }

    async def get_tool_usage(self, session_id: str | None = None) -> list[dict]:
        """Tool call count and average duration by tool name."""
        conn = self._ensure_conn()
        params: tuple[Any, ...] = ()
        where = "WHERE span_type = 'tool_call'"
        if session_id:
            where = "WHERE session_id = ? AND span_type = 'tool_call'"
            params = (session_id,)
        cursor = await conn.execute(
            f"""
            SELECT
                name,
                COUNT(*) as call_count,
                COALESCE(AVG(duration_ms), 0.0) as avg_duration_ms
            FROM execution_traces
            {where}
            GROUP BY name
            ORDER BY call_count DESC, name ASC
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "tool_name": row["name"],
                "count": row["call_count"],
                "avg_duration_ms": round(row["avg_duration_ms"], 2),
            }
            for row in rows
        ]

    async def get_cost_report(
        self,
        session_id: str | None = None,
    ) -> dict:
        """Cost report grouped by session and model."""
        conn = self._ensure_conn()
        params: tuple[Any, ...] = ()
        where = "WHERE cost_usd IS NOT NULL"
        if session_id:
            where = "WHERE session_id = ? AND cost_usd IS NOT NULL"
            params = (session_id,)

        total_cursor = await conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM execution_traces {where}",
            params,
        )
        total_row = await total_cursor.fetchone()
        total = float(total_row["total"] or 0.0)

        by_session_cursor = await conn.execute(
            f"""
            SELECT session_id, COALESCE(SUM(cost_usd), 0.0) as total_cost
            FROM execution_traces
            {where}
            GROUP BY session_id
            ORDER BY total_cost DESC
            """,
            params,
        )
        by_session_rows = await by_session_cursor.fetchall()

        by_model_cursor = await conn.execute(
            f"""
            SELECT
                COALESCE(json_extract(metadata, '$.model'), 'unknown') as model,
                COALESCE(SUM(cost_usd), 0.0) as total_cost
            FROM execution_traces
            {where}
            GROUP BY model
            ORDER BY total_cost DESC
            """,
            params,
        )
        by_model_rows = await by_model_cursor.fetchall()
        return {
            "total_cost_usd": round(total, 8),
            "by_session": [
                {
                    "session_id": row["session_id"],
                    "cost_usd": round(float(row["total_cost"] or 0.0), 8),
                }
                for row in by_session_rows
            ],
            "by_model": [
                {
                    "model": row["model"],
                    "cost_usd": round(float(row["total_cost"] or 0.0), 8),
                }
                for row in by_model_rows
            ],
        }

    async def get_session_token_totals(self) -> dict[str, int]:
        """Per-session sum of token_input + token_output across all spans."""
        conn = self._ensure_conn()
        cursor = await conn.execute(
            """
            SELECT session_id,
                   COALESCE(
                       SUM(COALESCE(token_input, 0) + COALESCE(token_output, 0)),
                       0
                   ) AS total_tokens
            FROM execution_traces
            GROUP BY session_id
            """
        )
        rows = await cursor.fetchall()
        return {str(row["session_id"]): int(row["total_tokens"]) for row in rows}

    async def cleanup_old_traces(self, retention_days: int) -> int:
        conn = self._ensure_conn()
        cutoff = time.time() - (retention_days * 86400)
        cursor = await conn.execute(
            "DELETE FROM execution_traces WHERE started_at < ?", (cutoff,)
        )
        await conn.commit()
        return cursor.rowcount

    async def vacuum(self) -> None:
        conn = self._ensure_conn()
        await conn.execute("VACUUM")
        await conn.commit()

    @staticmethod
    def _row_to_span(row: aiosqlite.Row) -> Span:
        metadata_raw = row["metadata"] or "{}"
        try:
            metadata = json.loads(metadata_raw)
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return Span(
            id=row["id"],
            session_id=row["session_id"],
            correlation_id=row["correlation_id"],
            parent_span_id=row["parent_span_id"],
            span_type=SpanType(row["span_type"]),
            name=row["name"],
            input_summary=row["input_summary"] or "",
            output_summary=row["output_summary"] or "",
            status=SpanStatus(row["status"]),
            error_message=row["error_message"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            duration_ms=(
                int(row["duration_ms"]) if row["duration_ms"] is not None else None
            ),
            token_input=row["token_input"],
            token_output=row["token_output"],
            cost_usd=row["cost_usd"],
            metadata=metadata,
        )
