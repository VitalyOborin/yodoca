"""Unit tests for ExtensionContext API wrappers and guards."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.events.topics import SystemTopics
from core.extensions.context import ExtensionContext
from core.extensions.persistence.models import ProjectInfo, ThreadInfo


def _build_context(
    tmp_path: Path,
    *,
    event_bus=None,
    thread_manager=None,
    project_service=None,
    shutdown_event=None,
    restart_file_path: Path | None = None,
) -> ExtensionContext:
    router = MagicMock()
    router.notify_user = AsyncMock()
    router.invoke_agent = AsyncMock(return_value="agent-reply")
    router.invoke_agent_streamed = AsyncMock(return_value="stream-reply")
    router.invoke_agent_background = AsyncMock(return_value="background-reply")
    router.enrich_prompt = AsyncMock(return_value="enriched")
    router.subscribe = MagicMock()
    router.unsubscribe = MagicMock()
    router.handle_user_message = AsyncMock()

    threads = thread_manager or MagicMock()
    if not hasattr(threads, "list_threads"):
        threads.list_threads = AsyncMock(return_value=[])
    if not hasattr(threads, "get_thread"):
        threads.get_thread = AsyncMock(return_value=None)
    if not hasattr(threads, "update_thread"):
        threads.update_thread = AsyncMock(return_value=None)
    if not hasattr(threads, "archive_thread"):
        threads.archive_thread = AsyncMock(return_value=False)
    if not hasattr(threads, "get_thread_history"):
        threads.get_thread_history = AsyncMock(return_value=None)
    if not hasattr(threads, "thread_repository"):
        threads.thread_repository = MagicMock()

    return ExtensionContext(
        extension_id="ext",
        config={"alpha": 1},
        logger=MagicMock(),
        router=router,
        thread_manager=threads,
        project_service=project_service,
        get_extension=lambda ext_id: {"id": ext_id},
        data_dir_path=tmp_path / "sandbox" / "data" / "ext",
        shutdown_event=shutdown_event,
        resolved_tools=["tool"],
        resolved_instructions="instr",
        agent_model="model",
        model_router=MagicMock(),
        agent_id="ext-agent",
        event_bus=event_bus,
        agent_registry=MagicMock(),
        restart_file_path=restart_file_path,
    )


@pytest.mark.asyncio
async def test_notify_user_uses_event_bus_when_available(tmp_path: Path) -> None:
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    ctx = _build_context(tmp_path, event_bus=event_bus)

    await ctx.notify_user("hello", "web")

    event_bus.publish.assert_awaited_once_with(
        SystemTopics.USER_NOTIFY,
        "ext",
        {"text": "hello", "channel_id": "web"},
    )
    ctx._router.notify_user.assert_not_called()


@pytest.mark.asyncio
async def test_notify_user_falls_back_to_router_without_event_bus(
    tmp_path: Path,
) -> None:
    ctx = _build_context(tmp_path, event_bus=None)

    await ctx.notify_user("hello", "web")

    ctx._router.notify_user.assert_awaited_once_with("hello", "web")


@pytest.mark.asyncio
async def test_invoke_and_stream_wrappers_delegate_to_router(tmp_path: Path) -> None:
    ctx = _build_context(tmp_path)
    on_chunk = AsyncMock()
    on_tool = AsyncMock()

    assert await ctx.invoke_agent("q") == "agent-reply"
    assert (
        await ctx.invoke_agent_streamed("q", on_chunk=on_chunk, on_tool_call=on_tool)
        == "stream-reply"
    )
    assert await ctx.invoke_agent_background("q") == "background-reply"
    assert await ctx.enrich_prompt("q") == "enriched"


def test_subscribe_and_unsubscribe_delegate_to_router(tmp_path: Path) -> None:
    ctx = _build_context(tmp_path)

    def handler(*_args, **_kwargs) -> None:
        return None

    ctx.subscribe("event", handler)
    ctx.unsubscribe("event", handler)

    ctx._router.subscribe.assert_called_once_with("event", handler)
    ctx._router.unsubscribe.assert_called_once_with("event", handler)


@pytest.mark.asyncio
async def test_emit_and_request_agent_events(tmp_path: Path) -> None:
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    ctx = _build_context(tmp_path, event_bus=event_bus)

    await ctx.emit("topic.custom", {"x": 1}, correlation_id="cid-1")
    await ctx.request_agent_task("do", channel_id="web")
    await ctx.request_agent_background("silent", correlation_id="cid-2")

    calls = event_bus.publish.await_args_list
    assert calls[0].args == ("topic.custom", "ext", {"x": 1}, "cid-1")
    assert calls[1].args == (
        SystemTopics.AGENT_TASK,
        "ext",
        {"prompt": "do", "channel_id": "web"},
        None,
    )
    assert calls[2].args == (
        SystemTopics.AGENT_BACKGROUND,
        "ext",
        {"prompt": "silent", "correlation_id": "cid-2"},
        None,
    )


def test_subscribe_event_calls_event_bus_subscribe(tmp_path: Path) -> None:
    event_bus = MagicMock()
    handler = AsyncMock()
    ctx = _build_context(tmp_path, event_bus=event_bus)

    ctx.subscribe_event("topic.test", handler)

    event_bus.subscribe.assert_called_once_with("topic.test", handler, "ext")


def test_basic_properties_and_accessors(tmp_path: Path) -> None:
    ctx = _build_context(tmp_path)

    assert ctx.model_router is not None
    assert ctx.agent_registry is not None
    assert ctx.get_config("alpha") == 1
    assert ctx.get_config("missing", 7) == 7
    assert ctx.get_extension("x") == {"id": "x"}


@pytest.mark.asyncio
async def test_thread_api_wrappers_delegate(tmp_path: Path) -> None:
    thread = ThreadInfo(
        id="th-1",
        project_id=None,
        title=None,
        channel_id="web",
        created_at=100,
        last_active_at=100,
        is_archived=False,
    )
    threads = MagicMock()
    threads.list_threads = AsyncMock(return_value=[thread])
    threads.get_thread = AsyncMock(return_value=thread)
    threads.update_thread = AsyncMock(return_value=thread)
    threads.archive_thread = AsyncMock(return_value=True)
    threads.get_thread_history = AsyncMock(return_value=[{"role": "user"}])
    threads.thread_repository = MagicMock()
    threads.thread_repository.create_thread.return_value = thread
    ctx = _build_context(tmp_path, thread_manager=threads)

    assert await ctx.list_threads(project_id="p1", channel_id="web") == [thread]
    created = await ctx.create_thread(
        thread_id="th-1", channel_id="web", project_id=None, title="Title"
    )
    assert created == thread
    assert await ctx.get_thread("th-1") == thread
    assert await ctx.update_thread("th-1", title="Updated") == thread
    assert await ctx.archive_thread("th-1") is True
    assert await ctx.get_thread_history("th-1") == [{"role": "user"}]
    threads.thread_repository.create_thread.assert_called_once()


@pytest.mark.asyncio
async def test_create_and_update_thread_validate_project_exists(tmp_path: Path) -> None:
    threads = MagicMock()
    threads.update_thread = AsyncMock(return_value=None)
    threads.thread_repository = MagicMock()
    projects = MagicMock()
    projects.get_project.return_value = None
    ctx = _build_context(tmp_path, thread_manager=threads, project_service=projects)

    with pytest.raises(ValueError, match="Project p404 not found"):
        await ctx.create_thread(
            thread_id="th-1",
            channel_id="web",
            project_id="p404",
            title=None,
        )
    with pytest.raises(ValueError, match="Project p404 not found"):
        await ctx.update_thread("th-1", project_id="p404")


@pytest.mark.asyncio
async def test_project_api_without_service_handles_gracefully(tmp_path: Path) -> None:
    ctx = _build_context(tmp_path, project_service=None)

    assert await ctx.list_projects() == []
    assert await ctx.get_project("p1") is None
    assert await ctx.delete_project("p1") is False
    with pytest.raises(RuntimeError, match="Project service is not configured"):
        await ctx.create_project(name="Alpha")
    with pytest.raises(RuntimeError, match="Project service is not configured"):
        await ctx.update_project("p1", name="New")


@pytest.mark.asyncio
async def test_project_api_with_service_delegates(tmp_path: Path) -> None:
    project = ProjectInfo(
        id="p1",
        name="Alpha",
        instructions=None,
        agent_config={},
        files=[],
        created_at=100,
        updated_at=100,
    )
    projects = MagicMock()
    projects.list_projects.return_value = [project]
    projects.get_project.return_value = project
    projects.create_project.return_value = project
    projects.update_project.return_value = project
    projects.delete_project.return_value = True
    ctx = _build_context(tmp_path, project_service=projects)

    assert await ctx.list_projects() == [project]
    assert await ctx.get_project("p1") == project
    assert await ctx.create_project(name="Alpha") == project
    assert await ctx.update_project("p1", name="Alpha+") == project
    assert await ctx.delete_project("p1") is True


def test_data_dir_and_restart_requests(tmp_path: Path) -> None:
    explicit_restart = tmp_path / "sandbox" / ".explicit_restart"
    ctx = _build_context(tmp_path, restart_file_path=explicit_restart)

    assert ctx.data_dir.exists()
    ctx.request_restart()
    assert explicit_restart.exists()
    assert explicit_restart.read_text(encoding="utf-8") == "restart requested"


def test_request_restart_fallback_path(tmp_path: Path) -> None:
    ctx = _build_context(tmp_path, restart_file_path=None)
    ctx.request_restart()
    expected = ctx._data_dir_path.parent.parent / ".restart_requested"
    assert expected.exists()


def test_request_shutdown_sets_event(tmp_path: Path) -> None:
    shutdown_event = asyncio.Event()
    ctx = _build_context(tmp_path, shutdown_event=shutdown_event)

    ctx.request_shutdown()

    assert shutdown_event.is_set() is True


@pytest.mark.asyncio
async def test_secret_methods_delegate_to_secret_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.extensions import context as context_module

    get_secret = AsyncMock(return_value="secret-value")
    set_secret = AsyncMock()
    monkeypatch.setattr(context_module, "get_secret_async", get_secret)
    monkeypatch.setattr(context_module, "set_secret_async", set_secret)
    ctx = _build_context(tmp_path)

    assert await ctx.get_secret("TOKEN") == "secret-value"
    await ctx.set_secret("TOKEN", "x")
    get_secret.assert_awaited_once_with("TOKEN")
    set_secret.assert_awaited_once_with("TOKEN", "x")
