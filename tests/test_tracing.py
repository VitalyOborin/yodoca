"""Tests for Execution Tracing extension: storage CRUD, extension lifecycle, hooks."""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sandbox.extensions.tracing.main import TracingExtension
from sandbox.extensions.tracing.models import Span, SpanStatus, SpanType
from sandbox.extensions.tracing.storage import TracingStorage
from sandbox.extensions.tracing.tools import build_tools

# ---------------------------------------------------------------------------
# Storage CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_save_and_get_span(tmp_path: Path) -> None:
    """save_span persists a span that get_span can retrieve."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        span = Span(
            id="span-1",
            session_id="sess-1",
            span_type=SpanType.AGENT_INVOKE,
            name="orchestrator",
            input_summary="hello",
            status=SpanStatus.RUNNING,
        )
        await storage.save_span(span)
        loaded = await storage.get_span("span-1")
        assert loaded is not None
        assert loaded.id == "span-1"
        assert loaded.session_id == "sess-1"
        assert loaded.span_type == SpanType.AGENT_INVOKE
        assert loaded.name == "orchestrator"
        assert loaded.input_summary == "hello"
        assert loaded.status == SpanStatus.RUNNING
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_storage_update_span(tmp_path: Path) -> None:
    """update_span modifies status, output, duration."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        span = Span(
            id="span-2",
            session_id="sess-1",
            span_type=SpanType.AGENT_INVOKE,
            name="agent",
            status=SpanStatus.RUNNING,
            started_at=time.time(),
        )
        await storage.save_span(span)
        span.status = SpanStatus.COMPLETED
        span.output_summary = "done"
        span.completed_at = time.time()
        span.duration_ms = 42
        await storage.update_span(span)
        loaded = await storage.get_span("span-2")
        assert loaded is not None
        assert loaded.status == SpanStatus.COMPLETED
        assert loaded.output_summary == "done"
        assert loaded.duration_ms == 42
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_storage_get_span_not_found(tmp_path: Path) -> None:
    """get_span returns None for unknown span ID."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        result = await storage.get_span("nonexistent")
        assert result is None
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_storage_trace_tree(tmp_path: Path) -> None:
    """get_trace_tree returns all spans for a session ordered by started_at."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        t = time.time()
        for i in range(3):
            span = Span(
                id=f"span-{i}",
                session_id="sess-tree",
                span_type=SpanType.AGENT_INVOKE,
                name=f"step-{i}",
                status=SpanStatus.COMPLETED,
                started_at=t + i,
            )
            await storage.save_span(span)
        # Add a span for a different session — should not appear
        await storage.save_span(
            Span(id="other", session_id="other-sess", name="x", started_at=t)
        )
        tree = await storage.get_trace_tree("sess-tree")
        assert len(tree) == 3
        assert [s.id for s in tree] == ["span-0", "span-1", "span-2"]
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_storage_trace_stats(tmp_path: Path) -> None:
    """get_trace_stats returns correct aggregated counts."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        t = time.time()
        await storage.save_span(
            Span(
                id="s1",
                session_id="sess",
                status=SpanStatus.COMPLETED,
                started_at=t,
                duration_ms=100,
                token_input=10,
                token_output=20,
            )
        )
        await storage.save_span(
            Span(
                id="s2",
                session_id="sess",
                status=SpanStatus.ERROR,
                started_at=t + 1,
                error_message="fail",
            )
        )
        await storage.save_span(
            Span(
                id="s3", session_id="sess", status=SpanStatus.RUNNING, started_at=t + 2
            )
        )
        stats = await storage.get_trace_stats(session_id="sess")
        assert stats["total_spans"] == 3
        assert stats["completed"] == 1
        assert stats["errors"] == 1
        assert stats["running"] == 1
        assert stats["total_token_input"] == 10
        assert stats["total_token_output"] == 20
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_storage_get_session_token_totals(tmp_path: Path) -> None:
    """get_session_token_totals aggregates token_input + token_output per session."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        t = time.time()
        await storage.save_span(
            Span(
                id="a",
                session_id="s1",
                status=SpanStatus.COMPLETED,
                started_at=t,
                token_input=10,
                token_output=20,
            )
        )
        await storage.save_span(
            Span(
                id="b",
                session_id="s1",
                status=SpanStatus.COMPLETED,
                started_at=t + 1,
                token_input=5,
                token_output=5,
            )
        )
        await storage.save_span(
            Span(
                id="c",
                session_id="s2",
                status=SpanStatus.COMPLETED,
                started_at=t,
                token_input=100,
                token_output=0,
            )
        )
        totals = await storage.get_session_token_totals()
        assert totals["s1"] == 40
        assert totals["s2"] == 100
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_storage_cleanup_old_traces(tmp_path: Path) -> None:
    """cleanup_old_traces deletes spans older than retention_days."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        old_time = time.time() - (31 * 86400)  # 31 days ago
        await storage.save_span(Span(id="old", session_id="s", started_at=old_time))
        await storage.save_span(Span(id="new", session_id="s", started_at=time.time()))
        deleted = await storage.cleanup_old_traces(retention_days=30)
        assert deleted == 1
        assert await storage.get_span("old") is None
        assert await storage.get_span("new") is not None
    finally:
        await storage.close()


# ---------------------------------------------------------------------------
# Extension lifecycle tests
# ---------------------------------------------------------------------------


def _make_mock_context(tmp_path: Path) -> MagicMock:
    """Create a mock ExtensionContext with the minimum needed attributes."""
    ctx = MagicMock()
    ctx.data_dir = tmp_path / "data" / "tracing"
    ctx.data_dir.mkdir(parents=True, exist_ok=True)
    ctx.get_config = MagicMock(
        side_effect=lambda key, default=None: {
            "max_input_summary_len": 2000,
            "max_output_summary_len": 2000,
            "trace_tool_calls": True,
            "retention_days": 30,
        }.get(key, default)
    )
    ctx.emit = AsyncMock()
    ctx.register_trace_hook = MagicMock()
    return ctx


@pytest.mark.asyncio
async def test_extension_initialize_and_health(tmp_path: Path) -> None:
    """TracingExtension initializes storage and registers trace hook."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    assert ext.health_check() is True
    ctx.register_trace_hook.assert_called_once_with(ext)
    await ext.destroy()
    assert ext.health_check() is False


@pytest.mark.asyncio
async def test_extension_provides_tools(tmp_path: Path) -> None:
    """get_tools returns tracing analytics tools after initialization."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    tools = ext.get_tools()
    assert len(tools) == 5
    names = {getattr(t, "name", "") for t in tools}
    assert "tracing_get_last_trace" in names
    assert "tracing_get_session_stats" in names
    assert "tracing_get_tool_usage" in names
    assert "tracing_get_cost_report" in names
    assert "tracing_explain_last_turn" in names
    await ext.destroy()


# ---------------------------------------------------------------------------
# TraceHook invocation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_invoke_start_creates_span(tmp_path: Path) -> None:
    """on_invoke_start creates a running span and returns its ID."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    span_id = await ext.on_invoke_start("hello world", "sess-100", "orchestrator")
    assert span_id  # non-empty string
    span = await ext._storage.get_span(span_id)
    assert span is not None
    assert span.session_id == "sess-100"
    assert span.span_type == SpanType.AGENT_INVOKE
    assert span.name == "orchestrator"
    assert span.input_summary == "hello world"
    assert span.status == SpanStatus.RUNNING
    # EventBus emit was called
    ctx.emit.assert_called()
    await ext.destroy()


@pytest.mark.asyncio
async def test_hook_invoke_end_completes_span(tmp_path: Path) -> None:
    """on_invoke_end marks the span as completed with output."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    span_id = await ext.on_invoke_start("prompt", "sess-200", "agent")
    await ext.on_invoke_end(span_id, "response text")
    span = await ext._storage.get_span(span_id)
    assert span is not None
    assert span.status == SpanStatus.COMPLETED
    assert span.output_summary == "response text"
    assert span.duration_ms is not None
    assert span.duration_ms >= 0
    await ext.destroy()


@pytest.mark.asyncio
async def test_hook_invoke_end_with_error(tmp_path: Path) -> None:
    """on_invoke_end with error sets ERROR status and error_message."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    span_id = await ext.on_invoke_start("prompt", "sess-300", "agent")
    await ext.on_invoke_end(span_id, "", error="something broke")
    span = await ext._storage.get_span(span_id)
    assert span is not None
    assert span.status == SpanStatus.ERROR
    assert span.error_message == "something broke"
    await ext.destroy()


@pytest.mark.asyncio
async def test_hook_tool_call_creates_child_span(tmp_path: Path) -> None:
    """on_tool_call creates a child span under the parent."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    parent_id = await ext.on_invoke_start("prompt", "sess-400", "agent")
    tool_span_id = await ext.on_tool_call(parent_id, "kv_get", '{"key":"x"}')
    assert tool_span_id  # non-empty
    span = await ext._storage.get_span(tool_span_id)
    assert span is not None
    assert span.parent_span_id == parent_id
    assert span.span_type == SpanType.TOOL_CALL
    assert span.name == "kv_get"
    assert span.session_id == "sess-400"
    await ext.destroy()


@pytest.mark.asyncio
async def test_hook_tool_result_completes_tool_span(tmp_path: Path) -> None:
    """on_tool_result marks the tool span as completed."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    parent_id = await ext.on_invoke_start("prompt", "sess-500", "agent")
    tool_id = await ext.on_tool_call(parent_id, "kv_set", "")
    await ext.on_tool_result(tool_id, "ok")
    span = await ext._storage.get_span(tool_id)
    assert span is not None
    assert span.status == SpanStatus.COMPLETED
    assert span.output_summary == "ok"
    await ext.destroy()


# ---------------------------------------------------------------------------
# Hook failure resilience tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_invoke_end_unknown_span_is_noop(tmp_path: Path) -> None:
    """on_invoke_end with unknown span_id does not raise."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    # Should not raise
    await ext.on_invoke_end("nonexistent-span", "output")
    await ext.destroy()


@pytest.mark.asyncio
async def test_hook_tool_result_unknown_span_is_noop(tmp_path: Path) -> None:
    """on_tool_result with unknown span_id does not raise."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    await ext.on_tool_result("nonexistent", "result")
    await ext.destroy()


@pytest.mark.asyncio
async def test_hook_emit_failure_does_not_propagate(tmp_path: Path) -> None:
    """If context.emit raises, hook methods still complete without error."""
    ctx = _make_mock_context(tmp_path)
    ctx.emit = AsyncMock(side_effect=RuntimeError("bus down"))
    ext = TracingExtension()
    await ext.initialize(ctx)
    # Should not raise despite emit failure
    span_id = await ext.on_invoke_start("prompt", "sess-600", "agent")
    assert span_id  # span was still created
    await ext.on_invoke_end(span_id, "output")
    span = await ext._storage.get_span(span_id)
    assert span is not None
    assert span.status == SpanStatus.COMPLETED
    await ext.destroy()


@pytest.mark.asyncio
async def test_extension_execute_task_cleanup(tmp_path: Path) -> None:
    """execute_task('cleanup_old_traces') deletes old spans."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    # Insert old span
    old_span = Span(id="old-span", session_id="s", started_at=time.time() - 31 * 86400)
    await ext._storage.save_span(old_span)
    result = await ext.execute_task("cleanup_old_traces")
    assert result is not None
    assert "1" in result["text"]
    assert await ext._storage.get_span("old-span") is None
    await ext.destroy()


@pytest.mark.asyncio
async def test_input_summary_truncation(tmp_path: Path) -> None:
    """Input summary is truncated to max_input_summary_len."""
    ctx = _make_mock_context(tmp_path)
    ctx.get_config = MagicMock(
        side_effect=lambda key, default=None: {
            "max_input_summary_len": 10,
            "max_output_summary_len": 10,
            "trace_tool_calls": True,
            "retention_days": 30,
        }.get(key, default)
    )
    ext = TracingExtension()
    await ext.initialize(ctx)
    span_id = await ext.on_invoke_start("a" * 100, "sess", "agent")
    span = await ext._storage.get_span(span_id)
    assert span is not None
    assert len(span.input_summary) == 10
    await ext.destroy()


@pytest.mark.asyncio
async def test_budget_warning_and_exceeded_events(tmp_path: Path) -> None:
    """Budget events are emitted once when configured thresholds are crossed."""
    ctx = _make_mock_context(tmp_path)
    ctx.get_config = MagicMock(
        side_effect=lambda key, default=None: {
            "max_input_summary_len": 2000,
            "max_output_summary_len": 2000,
            "trace_tool_calls": True,
            "retention_days": 30,
            "warn_at_session_tokens": 1,
            "stop_at_session_tokens": 2,
            "pricing": {},
        }.get(key, default)
    )
    ext = TracingExtension()
    await ext.initialize(ctx)
    span_id = await ext.on_invoke_start("hello", "sess-budget", "agent")
    await ext.on_invoke_end(span_id, "world")
    topics = [call.args[0] for call in ctx.emit.await_args_list]
    assert "tracing.budget.warning" in topics
    assert "tracing.budget.exceeded" in topics
    await ext.destroy()


@pytest.mark.asyncio
async def test_budget_rehydrates_session_totals_from_db(tmp_path: Path) -> None:
    """After restart, session token totals are loaded from SQLite."""
    ctx = _make_mock_context(tmp_path)
    ext1 = TracingExtension()
    await ext1.initialize(ctx)
    await ext1._storage.save_span(
        Span(
            id="pre",
            session_id="sess-rehyd",
            span_type=SpanType.AGENT_INVOKE,
            name="agent",
            status=SpanStatus.COMPLETED,
            started_at=time.time(),
            token_input=30,
            token_output=70,
        )
    )
    await ext1.destroy()

    ext2 = TracingExtension()
    await ext2.initialize(ctx)
    assert ext2._session_tokens_total.get("sess-rehyd") == 100
    await ext2.destroy()


@pytest.mark.asyncio
async def test_extension_execute_task_unknown_returns_none(tmp_path: Path) -> None:
    """execute_task with an unknown name returns None."""
    ctx = _make_mock_context(tmp_path)
    ext = TracingExtension()
    await ext.initialize(ctx)
    assert await ext.execute_task("nonexistent_task") is None
    await ext.destroy()


@pytest.mark.asyncio
async def test_tracing_get_tool_usage_tool(tmp_path: Path) -> None:
    """tracing_get_tool_usage returns structured ToolUsageEntry list."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        t = time.time()
        await storage.save_span(
            Span(
                id="tc1",
                session_id="sess-tools",
                span_type=SpanType.TOOL_CALL,
                name="my_tool",
                status=SpanStatus.COMPLETED,
                started_at=t,
                duration_ms=10.0,
                token_input=1,
                token_output=2,
            )
        )
        tools = build_tools(storage)
        tc = next(x for x in tools if x.name == "tracing_get_tool_usage")
        tool_ctx = MagicMock()
        r = await tc.on_invoke_tool(tool_ctx, json.dumps({"session_id": "sess-tools"}))
        assert r.success is True
        assert len(r.usage) == 1
        assert r.usage[0].tool_name == "my_tool"
        assert r.usage[0].count == 1
        assert r.usage[0].avg_duration_ms == 10.0
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_tracing_get_cost_report_tool(tmp_path: Path) -> None:
    """tracing_get_cost_report returns SessionCostEntry and ModelCostEntry."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        t = time.time()
        await storage.save_span(
            Span(
                id="c1",
                session_id="sess-cost",
                span_type=SpanType.AGENT_INVOKE,
                name="agent",
                status=SpanStatus.COMPLETED,
                started_at=t,
                cost_usd=0.01,
                metadata={"model": "gpt-4o"},
            )
        )
        tools = build_tools(storage)
        tc = next(x for x in tools if x.name == "tracing_get_cost_report")
        tool_ctx = MagicMock()
        r = await tc.on_invoke_tool(tool_ctx, json.dumps({"session_id": "sess-cost"}))
        assert r.success is True
        assert r.total_cost_usd >= 0.01
        assert len(r.by_session) >= 1
        assert r.by_session[0].session_id == "sess-cost"
        assert len(r.by_model) >= 1
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_tracing_explain_last_turn_tool(tmp_path: Path) -> None:
    """tracing_explain_last_turn produces a human-readable summary."""
    storage = TracingStorage(tmp_path / "traces.db")
    await storage.initialize()
    try:
        t = time.time()
        root = Span(
            id="root",
            session_id="sess-exp",
            span_type=SpanType.AGENT_INVOKE,
            name="orchestrator",
            status=SpanStatus.RUNNING,
            started_at=t,
        )
        await storage.save_span(root)
        await storage.save_span(
            Span(
                id="tool1",
                session_id="sess-exp",
                parent_span_id="root",
                span_type=SpanType.TOOL_CALL,
                name="lookup",
                status=SpanStatus.COMPLETED,
                started_at=t + 0.1,
                token_input=2,
                token_output=3,
                cost_usd=0.0,
            )
        )
        tools = build_tools(storage)
        tc = next(x for x in tools if x.name == "tracing_explain_last_turn")
        tool_ctx = MagicMock()
        r = await tc.on_invoke_tool(tool_ctx, json.dumps({"session_id": "sess-exp"}))
        assert r.success is True
        assert "lookup" in r.explanation
        assert "tool calls" in r.explanation.lower()
    finally:
        await storage.close()
