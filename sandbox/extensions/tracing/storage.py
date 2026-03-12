"""TracingStorage: async SQLite storage for execution trace spans."""

import json
import time
from pathlib import Path

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
                 started_at, completed_at, duration_ms, token_input, token_output, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(span.metadata, ensure_ascii=False) if span.metadata else "{}",
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
                metadata = ?
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
                json.dumps(span.metadata, ensure_ascii=False) if span.metadata else "{}",
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

    async def get_trace_stats(
        self, session_id: str | None = None
    ) -> dict:
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
            "avg_duration_ms": round(row[6], 2),
            "top_tools": [{"name": r[0], "count": r[1]} for r in tool_rows],
        }

    async def cleanup_old_traces(self, retention_days: int) -> int:
        assert self._conn is not None
        cutoff = time.time() - (retention_days * 86400)
        cursor = await self._conn.execute(
            "DELETE FROM execution_traces WHERE started_at < ?", (cutoff,)
        )
        await self._conn.commit()
        return cursor.rowcount

    @staticmethod
    def _row_to_span(row: tuple) -> Span:
        metadata_raw = row[15] or "{}"
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
            metadata=metadata,
        )
