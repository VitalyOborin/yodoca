"""Coverage-focused tests for runtime/bootstrap helpers."""

import asyncio
import ctypes
import logging
import logging.handlers
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents import lifecycle, orchestrator
from core.extensions.contract import ExtensionState
from core.extensions.manifest import ExtensionManifest
from core.extensions.routing.scheduler_manager import SchedulerManager
from core.runner import (
    _build_event_bus,
    _build_loader_router,
    _configure_agent_mcp_and_context,
    _configure_thread,
    _create_agent,
    _wire_extensions,
    main,
    main_async,
)
from core.settings_models import AppSettings, EventBusSettings, LoggingSettings, ThreadSettings


@pytest.fixture
def _restore_root_logger() -> None:
    """Keep root logging state isolated between tests."""
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    for h in root.handlers[:]:
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    root.setLevel(level)
    for h in handlers:
        root.addHandler(h)


def test_resolve_instructions_returns_literal_when_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator, "_PROJECT_ROOT", tmp_path)
    assert orchestrator._resolve_instructions("  literal text  ") == "literal text"


def test_resolve_instructions_reads_plain_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = tmp_path / "plain.txt"
    prompt.write_text("  from-file  \n", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "_PROJECT_ROOT", tmp_path)
    assert orchestrator._resolve_instructions("plain.txt") == "from-file"


def test_resolve_instructions_renders_jinja_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = tmp_path / "prompt.jinja2"
    prompt.write_text("Capabilities: {{ capabilities }}", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "_PROJECT_ROOT", tmp_path)
    rendered = orchestrator._resolve_instructions(
        "prompt.jinja2", {"capabilities": "memory, web"}
    )
    assert rendered == "Capabilities: memory, web"


def test_create_orchestrator_agent_merges_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent_ctor = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(orchestrator, "Agent", fake_agent_ctor)
    monkeypatch.setattr(
        orchestrator,
        "_resolve_instructions",
        lambda *_args, **_kwargs: "resolved",
    )
    model_router = MagicMock()
    model_router.get_model.return_value = "model-x"

    result = orchestrator.create_orchestrator_agent(
        model_router=model_router,
        settings=AppSettings(),
        extension_tools=["ext1"],
        delegation_tools=["del1"],
        channel_tools=["chan1"],
        capabilities_summary="caps",
    )

    assert result == {"ok": True}
    model_router.get_model.assert_called_once_with("orchestrator")
    call_kwargs = fake_agent_ctor.call_args.kwargs
    assert call_kwargs["instructions"] == "resolved"
    assert call_kwargs["model"] == "model-x"
    assert call_kwargs["tools"] == ["ext1", "del1", "chan1"]
    assert call_kwargs["name"] == "Orchestrator"


def test_setup_logging_file_handler_only(
    tmp_path: Path, _restore_root_logger: None
) -> None:
    from core.logging_config import setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="DEBUG",
                log_to_console=False,
                max_bytes=2048,
                backup_count=2,
            ),
        ),
    )
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], logging.handlers.RotatingFileHandler)
    assert (tmp_path / "logs" / "app.log").exists()


def test_setup_logging_adds_console_when_enabled(
    tmp_path: Path, _restore_root_logger: None
) -> None:
    from core.logging_config import setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                log_to_console=True,
                level="INFO",
            ),
        ),
    )
    root = logging.getLogger()
    assert any(
        isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers
    )
    assert any(type(h) is logging.StreamHandler for h in root.handlers)


def test_reset_terminal_for_input_skips_non_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import terminal

    monkeypatch.setattr(terminal.sys.stdin, "isatty", lambda: False)
    win = MagicMock()
    nix = MagicMock()
    monkeypatch.setattr(terminal, "_reset_windows_console", win)
    monkeypatch.setattr(terminal, "_reset_unix_terminal", nix)
    terminal.reset_terminal_for_input()
    win.assert_not_called()
    nix.assert_not_called()


def test_reset_terminal_for_input_routes_to_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import terminal

    monkeypatch.setattr(terminal.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(terminal.sys, "platform", "win32")
    win = MagicMock()
    nix = MagicMock()
    monkeypatch.setattr(terminal, "_reset_windows_console", win)
    monkeypatch.setattr(terminal, "_reset_unix_terminal", nix)
    terminal.reset_terminal_for_input()
    win.assert_called_once()
    nix.assert_not_called()


def test_reset_terminal_for_input_routes_to_unix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import terminal

    monkeypatch.setattr(terminal.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(terminal.sys, "platform", "linux")
    win = MagicMock()
    nix = MagicMock()
    monkeypatch.setattr(terminal, "_reset_windows_console", win)
    monkeypatch.setattr(terminal, "_reset_unix_terminal", nix)
    terminal.reset_terminal_for_input()
    nix.assert_called_once()
    win.assert_not_called()


def test_reset_unix_terminal_invokes_stty(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import terminal

    run_mock = MagicMock()
    fake_subprocess = SimpleNamespace(run=run_mock)
    monkeypatch.setitem(sys.modules, "subprocess", fake_subprocess)
    terminal._reset_unix_terminal()
    run_mock.assert_called_once()
    args, kwargs = run_mock.call_args
    assert args[0] == ["stty", "sane"]
    assert kwargs["timeout"] == 2


def test_reset_windows_console_sets_sane_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import terminal

    class _Kernel32:
        def __init__(self) -> None:
            self.set_calls: list[tuple[int, int]] = []

        def GetStdHandle(self, _std_input: int) -> int:
            return 10

        def GetConsoleMode(self, _handle: int, mode_ref) -> int:
            mode_ref._obj.value = 0
            return 1

        def SetConsoleMode(self, handle: int, mode: int) -> int:
            self.set_calls.append((handle, mode))
            return 1

    kernel = _Kernel32()
    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(kernel32=kernel),
        c_ulong=ctypes.c_ulong,
        byref=ctypes.byref,
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    terminal._reset_windows_console()

    assert kernel.set_calls == [(10, 0x0007)]


def test_reset_windows_console_returns_on_invalid_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import terminal

    class _Kernel32:
        def GetStdHandle(self, _std_input: int) -> int:
            return -1

    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(kernel32=_Kernel32()),
        c_ulong=ctypes.c_ulong,
        byref=ctypes.byref,
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    terminal._reset_windows_console()


@pytest.mark.asyncio
async def test_start_lifecycle_loop_runs_periodic_cleanup() -> None:
    registry = MagicMock()
    registry.cleanup_expired.side_effect = [1, 0, 0]

    task = lifecycle.start_lifecycle_loop(registry, interval_seconds=0.01)
    await asyncio.sleep(0.03)
    task.cancel()
    await task

    assert registry.cleanup_expired.call_count >= 2


@pytest.mark.asyncio
async def test_start_lifecycle_loop_survives_cleanup_exception() -> None:
    registry = MagicMock()
    registry.cleanup_expired.side_effect = [RuntimeError("boom"), 0, 0]

    task = lifecycle.start_lifecycle_loop(registry, interval_seconds=0.01)
    await asyncio.sleep(0.03)
    task.cancel()
    await task

    assert registry.cleanup_expired.call_count >= 2


def test_scheduler_manager_start_initializes_next_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"scheduler": ExtensionState.ACTIVE}
    router = MagicMock()
    manager = SchedulerManager(state=state, router=router)
    manifest = ExtensionManifest.model_validate(
        {
            "id": "scheduler",
            "name": "Scheduler",
            "entrypoint": "main:Ext",
            "schedules": [{"name": "tick", "cron": "* * * * *"}],
        }
    )
    manager.register("scheduler", AsyncMock(), manifest)
    start_mock = MagicMock()
    monkeypatch.setattr(manager._tasks, "start", start_mock)

    manager.start()

    assert "scheduler::tick" in manager._task_next
    start_mock.assert_called_once()


def test_scheduler_manager_start_falls_back_on_invalid_cron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"scheduler": ExtensionState.ACTIVE}
    router = MagicMock()
    manager = SchedulerManager(state=state, router=router)
    manifest = ExtensionManifest.model_validate(
        {
            "id": "scheduler",
            "name": "Scheduler",
            "entrypoint": "main:Ext",
            "schedules": [{"name": "bad", "cron": "not-a-cron"}],
        }
    )
    manager.register("scheduler", AsyncMock(), manifest)
    monkeypatch.setattr(manager._tasks, "start", MagicMock())
    monkeypatch.setattr(
        "core.extensions.routing.scheduler_manager.time.time",
        lambda: 1000.0,
    )

    manager.start()

    assert manager._task_next["scheduler::bad"] == 1000.0 + 86400


@pytest.mark.asyncio
async def test_scheduler_loop_executes_due_task_and_notifies_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"scheduler": ExtensionState.ACTIVE}
    router = MagicMock()
    router.notify_user = AsyncMock()
    manager = SchedulerManager(state=state, router=router)
    ext = AsyncMock()
    ext.execute_task = AsyncMock(return_value={"text": "scheduled"})
    manifest = ExtensionManifest.model_validate(
        {
            "id": "scheduler",
            "name": "Scheduler",
            "entrypoint": "main:Ext",
            "schedules": [{"name": "tick", "cron": "* * * * *"}],
        }
    )
    manager.register("scheduler", ext, manifest)
    manager._task_next["scheduler::tick"] = 0.0

    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(
        "core.extensions.routing.scheduler_manager.asyncio.sleep",
        sleep_mock,
    )
    monkeypatch.setattr(
        "core.extensions.routing.scheduler_manager.time.time",
        lambda: 1200.0,
    )

    with pytest.raises(asyncio.CancelledError):
        await manager._loop()

    ext.execute_task.assert_awaited_once_with("tick")
    router.notify_user.assert_awaited_once_with("scheduled")
    assert manager._task_next["scheduler::tick"] > 0.0


def test_runner_build_helpers_and_configure_calls(tmp_path: Path) -> None:
    settings = AppSettings(
        event_bus=EventBusSettings(
            db_path="sandbox/data/custom_event_journal.db",
            poll_interval=1.5,
            batch_size=7,
            max_retries=4,
            busy_timeout=2000,
            stale_timeout=42,
        ),
        thread=ThreadSettings(timeout_sec=55),
    )
    event_bus = _build_event_bus(settings)
    assert event_bus._poll_interval == 1.5
    assert event_bus._batch_size == 7
    assert event_bus._max_retries == 4
    assert event_bus._stale_timeout == 42

    loader, router, ext_dir, data_dir, shutdown_event = _build_loader_router(settings)
    assert ext_dir.name == "extensions"
    assert data_dir.name == "data"
    assert shutdown_event.is_set() is False
    assert hasattr(loader, "set_shutdown_event")
    assert hasattr(router, "configure_thread")

    router_mock = MagicMock()
    _configure_thread(router_mock, settings, tmp_path, event_bus)
    router_mock.configure_thread.assert_called_once()
    kwargs = router_mock.configure_thread.call_args.kwargs
    assert kwargs["thread_timeout"] == 55
    assert kwargs["event_bus"] is event_bus
    thread_db_path = Path(kwargs["thread_db_path"])
    assert thread_db_path.name == "thread.db"
    assert thread_db_path.parent.name == "memory"


@pytest.mark.asyncio
async def test_wire_extensions_calls_loader_pipeline() -> None:
    loader = MagicMock()
    loader.discover = AsyncMock()
    loader.load_all = AsyncMock()
    loader.initialize_all = AsyncMock()
    loader._update_setup_providers_state = AsyncMock()
    loader.detect_and_wire_all = MagicMock()
    loader.wire_event_subscriptions = MagicMock()
    router = MagicMock()
    event_bus = MagicMock()

    await _wire_extensions(loader, router, event_bus)

    loader.discover.assert_awaited_once()
    loader.load_all.assert_awaited_once()
    loader.initialize_all.assert_awaited_once_with(router)
    loader._update_setup_providers_state.assert_awaited_once()
    loader.detect_and_wire_all.assert_called_once_with(router)
    loader.wire_event_subscriptions.assert_called_once_with(event_bus)


def test_create_agent_builds_orchestrator_with_combined_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = MagicMock()
    loader.resolve_tools = MagicMock(return_value=["resolved"])
    loader.get_all_tools.return_value = ["ext-tool"]
    loader.get_available_tool_ids = MagicMock(return_value=["id1"])
    loader.get_capabilities_summary.return_value = "caps"
    router = MagicMock()
    event_bus = MagicMock()
    model_router = MagicMock()
    registry = MagicMock()
    agent_factory_cls = MagicMock(return_value="factory")
    delegation_tools = ["delegate-tool"]
    orchestrator_agent = MagicMock()

    monkeypatch.setattr("core.agents.factory.AgentFactory", agent_factory_cls)
    monkeypatch.setattr(
        "core.runner.make_channel_tools",
        lambda _router: ["channel-tool"],
    )
    monkeypatch.setattr(
        "core.runner.make_secure_input_tool",
        lambda _eb: "secure-tool",
    )
    monkeypatch.setattr(
        "core.runner.make_configure_extension_tool",
        lambda _ext, **_kw: "configure-extension-tool",
    )
    monkeypatch.setattr(
        "core.runner.make_delegation_tools",
        lambda *_a, **_k: delegation_tools,
    )
    monkeypatch.setattr(
        "core.runner.create_orchestrator_agent",
        lambda **kwargs: kwargs,
    )

    result = _create_agent(
        loader,
        router,
        event_bus,
        AppSettings(models={}),
        model_router,
        registry,
    )

    assert result["extension_tools"] == ["ext-tool"]
    assert result["delegation_tools"] == delegation_tools
    assert result["channel_tools"] == [
        "channel-tool",
        "secure-tool",
        "configure-extension-tool",
    ]
    assert result["capabilities_summary"] == "caps"
    agent_factory_cls.assert_called_once()
    assert orchestrator_agent is not None


@pytest.mark.asyncio
async def test_main_async_runs_bootstrap_and_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(thread=ThreadSettings(timeout_sec=10))
    model_router = MagicMock()
    model_router.remove_agent_config = MagicMock()
    registry = MagicMock()
    loader = MagicMock()
    loader.set_model_router = MagicMock()
    loader.set_agent_registry = MagicMock()
    loader.set_event_bus = MagicMock()
    loader.start_all = AsyncMock()
    loader.shutdown = AsyncMock()
    router = MagicMock()
    router.set_agent = MagicMock()
    data_dir = Path("sandbox/data")
    shutdown_event = asyncio.Event()
    shutdown_event.set()
    event_bus = MagicMock()
    event_bus.recover = AsyncMock()
    event_bus.start = AsyncMock()
    event_bus.stop = AsyncMock()
    agent = MagicMock()

    async def _forever() -> None:
        await asyncio.Event().wait()

    lifecycle_task = asyncio.create_task(_forever())
    monkeypatch.setattr("core.runner.load_settings", lambda: settings)
    monkeypatch.setattr("core.runner.setup_logging", lambda *_a, **_k: None)
    monkeypatch.setattr("core.runner.ModelRouter", lambda **_k: model_router)
    monkeypatch.setattr("core.runner.AgentRegistry", lambda **_k: registry)
    monkeypatch.setattr(
        "core.runner._build_loader_router",
        lambda _s: (loader, router, Path("ext"), data_dir, shutdown_event),
    )
    monkeypatch.setattr("core.runner._build_event_bus", lambda _s: event_bus)
    monkeypatch.setattr("core.runner._configure_thread", lambda *_a, **_k: None)
    monkeypatch.setattr("core.runner._wire_extensions", AsyncMock())
    monkeypatch.setattr("core.runner._create_agent", lambda *_a, **_k: agent)
    monkeypatch.setattr(
        "core.runner._configure_agent_mcp_and_context",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "core.runner.start_lifecycle_loop",
        lambda *_a, **_k: lifecycle_task,
    )

    await main_async()

    loader.set_model_router.assert_called_once_with(model_router)
    loader.set_agent_registry.assert_called_once_with(registry)
    loader.set_event_bus.assert_called_once_with(event_bus)
    router.set_agent.assert_called_once_with(agent)
    event_bus.recover.assert_awaited_once()
    event_bus.start.assert_awaited_once()
    loader.start_all.assert_awaited_once()
    event_bus.stop.assert_awaited_once()
    loader.shutdown.assert_awaited_once()
    assert lifecycle_task.cancelled() is True


def test_main_calls_asyncio_run_and_handles_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agents

    trace_mock = MagicMock()

    def _raise_keyboard_interrupt(coro) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(agents, "set_tracing_disabled", trace_mock)
    monkeypatch.setattr("core.runner.load_dotenv", MagicMock())
    monkeypatch.setattr("core.runner.reset_terminal_for_input", MagicMock())
    monkeypatch.setattr(
        "core.runner.asyncio.run",
        MagicMock(side_effect=_raise_keyboard_interrupt),
    )

    main()

    trace_mock.assert_called_once_with(True)


def test_main_calls_asyncio_run_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import agents

    trace_mock = MagicMock()
    run_mock = MagicMock(side_effect=lambda coro: coro.close())
    monkeypatch.setattr(agents, "set_tracing_disabled", trace_mock)
    monkeypatch.setattr("core.runner.load_dotenv", MagicMock())
    monkeypatch.setattr("core.runner.reset_terminal_for_input", MagicMock())
    monkeypatch.setattr("core.runner.asyncio.run", run_mock)

    main()

    trace_mock.assert_called_once_with(True)
    run_mock.assert_called_once()


def test_configure_agent_mcp_and_context() -> None:
    loader = MagicMock()
    router = MagicMock()
    agent = SimpleNamespace()
    loader.get_mcp_servers.return_value = ["srv"]

    _configure_agent_mcp_and_context(loader, router, agent)
    assert agent.mcp_servers == ["srv"]
    assert agent.mcp_config == {"convert_schemas_to_strict": True}
    loader.wire_context_providers.assert_called_once_with(router)
