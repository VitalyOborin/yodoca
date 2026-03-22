"""Coverage-focused tests for runtime/bootstrap helpers."""

import asyncio
import ctypes
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents import lifecycle
from core.extensions.contract import AgentProvider, ExtensionState
from core.extensions.manifest import ExtensionManifest
from core.extensions.routing.scheduler_manager import SchedulerManager
from core.runner import (
    _build_event_bus,
    _build_loader_router,
    _configure_agent_mcp_and_context,
    _configure_thread,
    _resolve_default_agent,
    _wire_extensions,
    main,
    main_async,
)
from core.settings_models import (
    AppSettings,
    EventBusSettings,
    LoggingSettings,
    ThreadSettings,
)


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


def test_resolve_default_agent_merges_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_model_settings = object()
    inner = MagicMock()
    inner.tools = []
    inner.model_settings = sentinel_model_settings
    ext = MagicMock(spec=AgentProvider)
    ext.agent = inner
    loader = MagicMock()
    loader.get_extensions.return_value = {"orchestrator_agent": ext}
    loader.get_all_tools.return_value = ["ext-tool"]
    loader.resolve_tools = MagicMock(return_value=[])
    loader.get_available_tool_ids = MagicMock(return_value=["id1"])
    loader.get_tool_catalog = MagicMock()
    router = MagicMock()
    event_bus = MagicMock()
    model_router = MagicMock()
    registry = MagicMock()
    agent_factory_cls = MagicMock(return_value="factory")
    delegation_tools = ["delegate-tool"]

    monkeypatch.setattr("core.runner.AgentFactory", agent_factory_cls)
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
    result = _resolve_default_agent(
        loader,
        router,
        event_bus,
        AppSettings(default_agent="orchestrator_agent", models={}),
        model_router,
        registry,
    )

    assert result is inner
    assert inner.tools == [
        "ext-tool",
        "delegate-tool",
        "channel-tool",
        "secure-tool",
        "configure-extension-tool",
    ]
    agent_factory_cls.assert_called_once()
    assert inner.model_settings is sentinel_model_settings


def test_resolve_default_agent_requires_configuration() -> None:
    loader = MagicMock()
    loader.get_extensions.return_value = {}
    with pytest.raises(RuntimeError, match="default_agent not configured"):
        _resolve_default_agent(
            loader,
            MagicMock(),
            MagicMock(),
            AppSettings(default_agent=""),
            MagicMock(),
            MagicMock(),
        )


def test_resolve_default_agent_missing_extension() -> None:
    loader = MagicMock()
    loader.get_extensions.return_value = {}
    with pytest.raises(RuntimeError, match="not found in loaded extensions"):
        _resolve_default_agent(
            loader,
            MagicMock(),
            MagicMock(),
            AppSettings(default_agent="orchestrator_agent", models={}),
            MagicMock(),
            MagicMock(),
        )


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
    assert len(root.handlers) == 2
    assert (
        sum(
            1
            for h in root.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        == 1
    )
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
    assert len(root.handlers) == 3


def test_subsystem_level_override(tmp_path: Path, _restore_root_logger: None) -> None:
    from core.logging_config import setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="INFO",
                log_to_console=False,
                subsystems={"ext.memory": "DEBUG"},
            ),
        ),
    )
    log_path = tmp_path / "logs" / "app.log"
    logging.getLogger("ext.memory").debug("mem-debug")
    logging.getLogger("ext.other").debug("other-debug")
    text = log_path.read_text(encoding="utf-8")
    assert "mem-debug" in text
    assert "other-debug" not in text


def test_console_level_independent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], _restore_root_logger: None
) -> None:
    from core.logging_config import setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="INFO",
                console_level="WARNING",
                log_to_console=True,
            ),
        ),
    )
    logging.getLogger("demo").info("to-file-not-console")
    logging.getLogger("demo").warning("to-both")
    err = capsys.readouterr().err
    assert "to-file-not-console" not in err
    assert "to-both" in err
    log_path = tmp_path / "logs" / "app.log"
    body = log_path.read_text(encoding="utf-8")
    assert "to-file-not-console" in body
    assert "to-both" in body


def test_json_console_formatter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], _restore_root_logger: None
) -> None:
    from core.logging_config import setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="INFO",
                log_to_console=True,
                console_style="json",
            ),
        ),
    )
    logging.getLogger("jsoncon").info("hello-json")
    err = capsys.readouterr().err.strip()
    row = json.loads(err)
    assert row["level"] == "INFO"
    assert row["logger"] == "jsoncon"
    assert row["message"] == "hello-json"
    assert "timestamp" in row


def test_json_file_formatter(tmp_path: Path, _restore_root_logger: None) -> None:
    from core.logging_config import setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="INFO",
                log_to_console=False,
                file_style="json",
            ),
        ),
    )
    logging.getLogger("jf").warning("w")
    log_path = tmp_path / "logs" / "app.log"
    line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    row = json.loads(line)
    assert row["level"] == "WARNING"
    assert row["logger"] == "jf"
    assert row["message"] == "w"


def test_meta_in_json_output(tmp_path: Path, _restore_root_logger: None) -> None:
    from core.logging_config import create_subsystem_logger, setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="INFO",
                log_to_console=False,
                file_style="json",
            ),
        ),
    )
    create_subsystem_logger("meta.sub").info("x", meta={"request_id": "abc"})
    log_path = tmp_path / "logs" / "app.log"
    line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    row = json.loads(line)
    assert row["meta"] == {"request_id": "abc"}


def test_child_logger(tmp_path: Path, _restore_root_logger: None) -> None:
    from core.logging_config import create_subsystem_logger, setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="INFO",
                log_to_console=False,
                subsystems={"ext.memory": "DEBUG"},
            ),
        ),
    )
    parent = create_subsystem_logger("ext.memory")
    child = parent.child("query")
    assert child.unwrap.name == "ext.memory.query"
    child.debug("q-debug")
    log_path = tmp_path / "logs" / "app.log"
    assert "q-debug" in log_path.read_text(encoding="utf-8")


def test_subsystem_filter_prefix_match(
    tmp_path: Path, _restore_root_logger: None
) -> None:
    from core.logging_config import setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="INFO",
                log_to_console=False,
                subsystems={"ext.memory": "DEBUG"},
            ),
        ),
    )
    logging.getLogger("ext.memory.tools").debug("tools-debug")
    log_path = tmp_path / "logs" / "app.log"
    assert "tools-debug" in log_path.read_text(encoding="utf-8")


def test_is_enabled(tmp_path: Path, _restore_root_logger: None) -> None:
    from core.logging_config import create_subsystem_logger, setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="INFO",
                console_level="DEBUG",
                log_to_console=True,
            ),
        ),
    )
    log = create_subsystem_logger("is.en")
    assert log.is_enabled("debug", "console") is True
    assert log.is_enabled("debug", "file") is False

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="DEBUG",
                log_to_console=True,
                console_level="INFO",
            ),
        ),
    )
    log2 = create_subsystem_logger("is.en2")
    assert log2.is_enabled("debug", "console") is False
    assert log2.is_enabled("debug", "file") is True


def test_console_subsystems_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], _restore_root_logger: None
) -> None:
    from core.logging_config import setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log",
                level="INFO",
                log_to_console=True,
                console_subsystems=["ext.memory"],
            ),
        ),
    )
    logging.getLogger("ext.memory").info("mem-msg")
    logging.getLogger("ext.other").info("other-msg")
    err = capsys.readouterr().err
    assert "mem-msg" in err
    assert "other-msg" not in err


def test_register_transport(tmp_path: Path, _restore_root_logger: None) -> None:
    from core.logging_config import register_log_transport, setup_logging

    setup_logging(
        tmp_path,
        AppSettings(
            logging=LoggingSettings(
                file="logs/app.log", level="INFO", log_to_console=False
            ),
        ),
    )
    received: list[str] = []

    def cb(record: logging.LogRecord) -> None:
        received.append(record.getMessage())

    unreg = register_log_transport(cb)
    logging.getLogger("tr").info("one")
    assert received == ["one"]
    unreg()
    logging.getLogger("tr").info("two")
    assert received == ["one"]


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
    monkeypatch.setattr("core.runner._resolve_default_agent", lambda *_a, **_k: agent)
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
    router.set_agent.assert_called_once_with(agent, agent_id=settings.default_agent)
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
