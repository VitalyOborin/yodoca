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
    duration_ms     REAL,
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

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
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
        assert self._conn is not None
        await self._conn.execute(
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
        await self._conn.commit()

    async def update_span(self, span: Span) -> None:
        assert self._conn is not None
        await self._conn.execute(
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
        await self._conn.commit()

    async def get_span(self, span_id: str) -> Span | None:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT * FROM execution_traces WHERE id = ?", (span_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_span(row)

    async def get_trace_tree(self, session_id: str) -> list[Span]:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT * FROM execution_traces WHERE session_id = ? ORDER BY started_at",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_span(r) for r in rows]

    async def get_trace_stats(self, session_id: str | None = None) -> dict:
        assert self._conn is not None
        where = ""
        params: tuple = ()
        if session_id:
            where = "WHERE session_id = ?"
            params = (session_id,)

        cursor = await self._conn.execute(
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

        tool_cursor = await self._conn.execute(
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
            "total_spans": row[0],
            "completed": row[1],
            "errors": row[2],
            "running": row[3],
            "total_token_input": row[4],
            "total_token_output": row[5],
            "total_cost_usd": round(row[6], 8),
            "avg_duration_ms": round(row[7], 2),
            "top_tools": [{"name": r[0], "count": r[1]} for r in tool_rows],
        }

    async def get_last_trace(self, session_id: str) -> list[Span]:
        """Return the latest agent invocation trace tree for a session."""
        assert self._conn is not None
        cursor = await self._conn.execute(
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
        root_id = row[0]
        cursor = await self._conn.execute(
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
        assert self._conn is not None
        params: tuple[Any, ...] = ()
        where = "WHERE span_type = 'tool_call'"
        if session_id:
            where = "WHERE session_id = ? AND span_type = 'tool_call'"
            params = (session_id,)
        cursor = await self._conn.execute(
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
            {"tool_name": row[0], "count": row[1], "avg_duration_ms": round(row[2], 2)}
            for row in rows
        ]

    async def get_cost_report(
        self,
        session_id: str | None = None,
    ) -> dict:
        """Cost report grouped by session and model."""
        assert self._conn is not None
        params: tuple[Any, ...] = ()
        where = "WHERE cost_usd IS NOT NULL"
        if session_id:
            where = "WHERE session_id = ? AND cost_usd IS NOT NULL"
            params = (session_id,)

        total_cursor = await self._conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0.0) FROM execution_traces {where}",
            params,
        )
        total_row = await total_cursor.fetchone()
        total = float(total_row[0] or 0.0)

        by_session_cursor = await self._conn.execute(
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

        by_model_cursor = await self._conn.execute(
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
                {"session_id": row[0], "cost_usd": round(float(row[1] or 0.0), 8)}
                for row in by_session_rows
            ],
            "by_model": [
                {"model": row[0], "cost_usd": round(float(row[1] or 0.0), 8)}
                for row in by_model_rows
            ],
        }

    async def cleanup_old_traces(self, retention_days: int) -> int:
        assert self._conn is not None
        cutoff = time.time() - (retention_days * 86400)
        cursor = await self._conn.execute(
            "DELETE FROM execution_traces WHERE started_at < ?", (cutoff,)
        )
        await self._conn.commit()
        return cursor.rowcount

    async def vacuum(self) -> None:
        assert self._conn is not None
        await self._conn.execute("VACUUM")
        await self._conn.commit()

    @staticmethod
    def _row_to_span(row: tuple) -> Span:
        metadata_raw = row[16] or "{}"
        try:
            metadata = json.loads(metadata_raw)
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return Span(
            id=row[0],
            session_id=row[1],
            correlation_id=row[2],
            parent_span_id=row[3],
            span_type=SpanType(row[4]),
            name=row[5],
            input_summary=row[6] or "",
            output_summary=row[7] or "",
            status=SpanStatus(row[8]),
            error_message=row[9],
            started_at=row[10],
            completed_at=row[11],
            duration_ms=row[12],
            token_input=row[13],
            token_output=row[14],
            cost_usd=row[15],
            metadata=metadata,
        )
