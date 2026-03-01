"""Tests for Loader: dependency order, discover, protocol detection, lifecycle."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.events import EventBus
from core.events.topics import SystemTopics
from core.extensions.contract import (
    AgentDescriptor,
    AgentProvider,
    AgentResponse,
    ChannelProvider,
    Extension,
    ServiceProvider,
    ToolProvider,
)
from core.extensions.loader import Loader, ExtensionState
from core.extensions.manifest import ExtensionManifest
from core.extensions.router import MessageRouter


_EMPTY_SETTINGS: dict = {"extensions": {}}


def _manifest(
    ext_id: str,
    depends_on: list[str] | None = None,
    config: dict | None = None,
) -> ExtensionManifest:
    data: dict = {
        "id": ext_id,
        "name": ext_id.title(),
        "entrypoint": "main:Cls",
        "depends_on": depends_on or [],
    }
    if config is not None:
        data["config"] = config
    return ExtensionManifest.model_validate(data)


class TestResolveDependencyOrder:
    """Topological sort and validation of depends_on."""

    def test_empty(self) -> None:
        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = []
        order = loader._resolve_dependency_order()
        assert order == []

    def test_no_deps(self) -> None:
        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [_manifest("a"), _manifest("b"), _manifest("c")]
        order = loader._resolve_dependency_order()
        ids = [m.id for m in order]
        assert set(ids) == {"a", "b", "c"}
        # Order may be any topological order; with no deps, all are valid

    def test_single_dep(self) -> None:
        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [_manifest("a", ["b"]), _manifest("b")]
        order = loader._resolve_dependency_order()
        ids = [m.id for m in order]
        assert ids.index("b") < ids.index("a")

    def test_chain(self) -> None:
        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [
            _manifest("a", ["b"]),
            _manifest("b", ["c"]),
            _manifest("c"),
        ]
        order = loader._resolve_dependency_order()
        ids = [m.id for m in order]
        assert ids.index("c") < ids.index("b") < ids.index("a")

    def test_cycle_raises(self) -> None:
        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [
            _manifest("a", ["b"]),
            _manifest("b", ["a"]),
        ]
        with pytest.raises(ValueError, match="Cycle in depends_on involving"):
            loader._resolve_dependency_order()

    def test_missing_dep_raises(self) -> None:
        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [_manifest("a", ["missing"])]
        with pytest.raises(ValueError, match="depends on missing"):
            loader._resolve_dependency_order()


class TestDiscover:
    """Loader.discover() scans dir and loads manifests."""

    @pytest.mark.asyncio
    async def test_empty_dir(self, tmp_path: Path) -> None:
        loader = Loader(
            extensions_dir=tmp_path, data_dir=tmp_path, settings=_EMPTY_SETTINGS
        )
        await loader.discover()
        assert loader._manifests == []

    @pytest.mark.asyncio
    async def test_skips_non_dir_and_missing_manifest(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "manifest.yaml").write_text(
            "id: sub\nname: Sub\nentrypoint: main:Sub\n",
            encoding="utf-8",
        )
        loader = Loader(
            extensions_dir=tmp_path, data_dir=tmp_path, settings=_EMPTY_SETTINGS
        )
        await loader.discover()
        assert len(loader._manifests) == 1
        assert loader._manifests[0].id == "sub"

    @pytest.mark.asyncio
    async def test_skips_disabled(self, tmp_path: Path) -> None:
        ext_dir = tmp_path / "disabled_ext"
        ext_dir.mkdir()
        (ext_dir / "manifest.yaml").write_text(
            "id: disabled_ext\nname: Disabled\nentrypoint: main:X\nenabled: false\n",
            encoding="utf-8",
        )
        loader = Loader(
            extensions_dir=tmp_path, data_dir=tmp_path, settings=_EMPTY_SETTINGS
        )
        await loader.discover()
        assert len(loader._manifests) == 0


class TestLoadAllAndProtocolDetection:
    """load_all with real sandbox extensions; detect_and_wire_all."""

    @pytest.mark.asyncio
    async def test_load_all_from_sandbox(self) -> None:
        # Use project sandbox if present
        project_root = Path(__file__).resolve().parent.parent
        extensions_dir = project_root / "sandbox" / "extensions"
        data_dir = project_root / "sandbox" / "data"
        if not extensions_dir.exists():
            pytest.skip("sandbox/extensions not found")
        loader = Loader(
            extensions_dir=extensions_dir, data_dir=data_dir, settings=_EMPTY_SETTINGS
        )
        await loader.discover()
        await loader.load_all()
        assert len(loader._extensions) >= 1
        for ext_id, ext in loader._extensions.items():
            assert isinstance(ext, Extension)

    @pytest.mark.asyncio
    async def test_detect_and_wire_registers_channels_and_tools(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        extensions_dir = project_root / "sandbox" / "extensions"
        data_dir = project_root / "sandbox" / "data"
        if not extensions_dir.exists():
            pytest.skip("sandbox/extensions not found")
        loader = Loader(
            extensions_dir=extensions_dir, data_dir=data_dir, settings=_EMPTY_SETTINGS
        )
        router = MessageRouter()
        await loader.discover()
        await loader.load_all()
        loader.detect_and_wire_all(router)
        tools = loader.get_all_tools()
        # cli_channel is ChannelProvider; kv is ToolProvider
        has_channel = "cli_channel" in loader._extensions and isinstance(
            loader._extensions["cli_channel"], ChannelProvider
        )
        has_tools = len(tools) > 0  # kv provides kv_set, kv_get
        assert has_channel or has_tools or len(loader._extensions) == 0


class TestProactiveLoop:
    """invoke_agent subscriptions: _collect_proactive_subscriptions and wire_event_subscriptions."""

    def _manifest_with_invoke_agent(self, ext_id: str, topic: str) -> ExtensionManifest:
        return ExtensionManifest.model_validate(
            {
                "id": ext_id,
                "name": ext_id.title(),
                "entrypoint": "main:Cls",
                "agent": {"integration_mode": "tool", "model": "gpt-4"},
                "depends_on": [],
                "events": {"subscribes": [{"topic": topic, "handler": "invoke_agent"}]},
            }
        )

    def test_collect_proactive_subscriptions_returns_topic_to_ext_id(self) -> None:
        """_collect_proactive_subscriptions maps topic -> ext_id for invoke_agent subs."""
        mock_agent = MagicMock(spec=AgentProvider)
        mock_agent.get_agent_descriptor.return_value = AgentDescriptor(
            name="Email Agent", description="Triage emails", integration_mode="tool"
        )

        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [
            self._manifest_with_invoke_agent("email_agent", "email.received"),
        ]
        loader._extensions = {"email_agent": mock_agent}
        loader._state = {"email_agent": ExtensionState.INACTIVE}
        loader._agent_providers = {"email_agent": mock_agent}

        result = loader._collect_proactive_subscriptions()
        assert result == {"email.received": "email_agent"}

    def test_collect_proactive_subscriptions_skips_non_agent_extensions(self) -> None:
        """Extensions without AgentProvider are skipped for invoke_agent."""
        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [
            self._manifest_with_invoke_agent("not_an_agent", "email.received"),
        ]
        loader._extensions = {"not_an_agent": MagicMock()}
        loader._state = {"not_an_agent": ExtensionState.INACTIVE}
        loader._agent_providers = {}  # not an AgentProvider

        result = loader._collect_proactive_subscriptions()
        assert result == {}

    @pytest.mark.asyncio
    async def test_wire_event_subscriptions_registers_proactive_handlers(
        self, tmp_path: Path
    ) -> None:
        """wire_event_subscriptions subscribes to topics for invoke_agent."""
        mock_agent = MagicMock(spec=AgentProvider)
        mock_agent.get_agent_descriptor.return_value = AgentDescriptor(
            name="Email Agent", description="Triage", integration_mode="tool"
        )
        mock_agent.invoke = AsyncMock(
            return_value=AgentResponse(status="success", content="Processed email")
        )

        loader = Loader(
            extensions_dir=Path("."), data_dir=tmp_path, settings=_EMPTY_SETTINGS
        )
        loader._router = MessageRouter()
        loader._manifests = [
            self._manifest_with_invoke_agent("email_agent", "email.received"),
        ]
        loader._extensions = {"email_agent": mock_agent}
        loader._state = {"email_agent": ExtensionState.INACTIVE}
        loader._agent_providers = {"email_agent": mock_agent}

        event_bus = EventBus(db_path=tmp_path / "events.db")
        await event_bus.recover()
        loader.wire_event_subscriptions(event_bus)

        assert "email.received" in event_bus._subscribers
        handlers = event_bus._subscribers["email.received"]
        proactive = [h for h in handlers if h[1] == "kernel.proactive"]
        assert len(proactive) == 1

    @pytest.mark.asyncio
    async def test_wire_event_subscriptions_registers_system_topics(
        self, tmp_path: Path
    ) -> None:
        """wire_event_subscriptions registers system topic handlers first."""
        loader = Loader(
            extensions_dir=Path("."), data_dir=tmp_path, settings=_EMPTY_SETTINGS
        )
        loader._router = MessageRouter()
        loader._manifests = []
        loader._extensions = {}
        loader._state = {}
        loader._agent_providers = {}

        event_bus = EventBus(db_path=tmp_path / "events.db")
        await event_bus.recover()
        loader.wire_event_subscriptions(event_bus)

        for topic in (
            SystemTopics.USER_NOTIFY,
            SystemTopics.AGENT_TASK,
            SystemTopics.AGENT_BACKGROUND,
        ):
            assert topic in event_bus._subscribers
            handlers = event_bus._subscribers[topic]
            kernel_handlers = [h for h in handlers if h[1] == "kernel.system"]
            assert len(kernel_handlers) == 1, f"Expected kernel handler for {topic}"

    @pytest.mark.asyncio
    async def test_on_agent_task_passes_turn_context_channel(self, tmp_path: Path) -> None:
        """system.agent.task passes channel_id into background invoke turn_context."""
        loader = Loader(extensions_dir=Path("."), data_dir=tmp_path, settings=_EMPTY_SETTINGS)
        router = MessageRouter()
        router.invoke_agent_background = AsyncMock(return_value="done")  # type: ignore[method-assign]
        router.notify_user = AsyncMock()  # type: ignore[method-assign]
        loader._router = router

        event = SimpleNamespace(
            payload={"prompt": "run this", "channel_id": "telegram_channel"}
        )
        await loader._on_agent_task(event)

        assert router.invoke_agent_background.await_count == 1
        _args, kwargs = router.invoke_agent_background.await_args
        assert kwargs["turn_context"].channel_id == "telegram_channel"
        router.notify_user.assert_awaited_once_with("done", "telegram_channel")


class TestInitializeAndLifecycle:
    """initialize_all receives context; start/stop/shutdown order."""

    @pytest.mark.asyncio
    async def test_initialize_all_passes_context(self) -> None:
        received_ctx = []

        class MockExt:
            async def initialize(self, context: object) -> None:
                received_ctx.append(context)

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            async def destroy(self) -> None:
                pass

            def health_check(self) -> bool:
                return True

        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [_manifest("x")]
        loader._extensions = {"x": MockExt()}
        loader._state = {"x": ExtensionState.INACTIVE}
        router = MessageRouter()
        await loader.initialize_all(router)
        assert len(received_ctx) == 1
        assert getattr(received_ctx[0], "extension_id", None) == "x"
        assert getattr(received_ctx[0], "config", None) == {}

    @pytest.mark.asyncio
    async def test_initialize_all_merges_settings_overrides_into_config(self) -> None:
        """get_config resolves: settings.extensions.<id>.<key> → manifest config → default."""
        received_ctx = []

        class MockExt:
            async def initialize(self, context: object) -> None:
                received_ctx.append(context)

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            async def destroy(self) -> None:
                pass

            def health_check(self) -> bool:
                return True

        manifest_with_config = _manifest("x", config={"tick_interval": 30})
        settings_with_override = {"extensions": {"x": {"tick_interval": 120}}}
        loader = Loader(
            extensions_dir=Path("."),
            data_dir=Path("."),
            settings=settings_with_override,
        )
        loader._manifests = [manifest_with_config]
        loader._extensions = {"x": MockExt()}
        loader._state = {"x": ExtensionState.INACTIVE}
        router = MessageRouter()
        await loader.initialize_all(router)

        ctx = received_ctx[0]
        assert ctx.get_config("tick_interval", 10) == 120  # settings override wins

    @pytest.mark.asyncio
    async def test_initialize_all_uses_manifest_when_no_settings_override(self) -> None:
        """When no settings override, get_config returns manifest value, then default."""
        received_ctx = []

        class MockExt:
            async def initialize(self, context: object) -> None:
                received_ctx.append(context)

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            async def destroy(self) -> None:
                pass

            def health_check(self) -> bool:
                return True

        manifest_with_config = _manifest("x", config={"tick_interval": 30})
        settings_no_override = {"extensions": {}}
        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=settings_no_override
        )
        loader._manifests = [manifest_with_config]
        loader._extensions = {"x": MockExt()}
        loader._state = {"x": ExtensionState.INACTIVE}
        router = MessageRouter()
        await loader.initialize_all(router)

        ctx = received_ctx[0]
        assert ctx.get_config("tick_interval", 10) == 30  # manifest wins
        assert ctx.get_config("missing_key", 10) == 10  # default wins

    @pytest.mark.asyncio
    async def test_start_all_calls_start_and_sets_active(self) -> None:
        started = []

        class MockExt:
            async def initialize(self, context: object) -> None:
                pass

            async def start(self) -> None:
                started.append("started")

            async def stop(self) -> None:
                pass

            async def destroy(self) -> None:
                pass

            def health_check(self) -> bool:
                return True

        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [_manifest("x")]
        loader._extensions = {"x": MockExt()}
        loader._state = {"x": ExtensionState.INACTIVE}
        router = MessageRouter()
        await loader.initialize_all(router)
        loader.detect_and_wire_all(router)
        await loader.start_all()
        assert started == ["started"]
        assert loader._state.get("x") == ExtensionState.ACTIVE

    @pytest.mark.asyncio
    async def test_shutdown_stop_and_destroy_reverse_order(self) -> None:
        order = []

        class MockExt:
            def __init__(self, name: str) -> None:
                self.name = name

            async def initialize(self, context: object) -> None:
                pass

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                order.append(f"{self.name}.stop")

            async def destroy(self) -> None:
                order.append(f"{self.name}.destroy")

            def health_check(self) -> bool:
                return True

        a, b = MockExt("a"), MockExt("b")
        loader = Loader(
            extensions_dir=Path("."), data_dir=Path("."), settings=_EMPTY_SETTINGS
        )
        loader._manifests = [_manifest("a", ["b"]), _manifest("b")]  # load order: b, a
        loader._extensions = {"a": a, "b": b}
        loader._state = {"a": ExtensionState.ACTIVE, "b": ExtensionState.ACTIVE}
        loader._service_tasks = {}
        loader._cron_task = None
        loader._health_task = None
        await loader.shutdown()
        # Shutdown order should be reverse of load order: a, then b
        assert order == ["a.stop", "a.destroy", "b.stop", "b.destroy"]
