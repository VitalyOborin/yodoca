"""Tests for Loader: dependency order, discover, protocol detection, lifecycle."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.extensions.contract import (
    ChannelProvider,
    Extension,
    ServiceProvider,
    ToolProvider,
)
from core.extensions.loader import Loader, ExtensionState
from core.extensions.manifest import ExtensionManifest
from core.extensions.router import MessageRouter


def _manifest(ext_id: str, depends_on: list[str] | None = None) -> ExtensionManifest:
    return ExtensionManifest.model_validate({
        "id": ext_id,
        "name": ext_id.title(),
        "entrypoint": "main:Cls",
        "depends_on": depends_on or [],
    })


class TestResolveDependencyOrder:
    """Topological sort and validation of depends_on."""

    def test_empty(self) -> None:
        loader = Loader(extensions_dir=Path("."), data_dir=Path("."))
        loader._manifests = []
        order = loader._resolve_dependency_order()
        assert order == []

    def test_no_deps(self) -> None:
        loader = Loader(extensions_dir=Path("."), data_dir=Path("."))
        loader._manifests = [_manifest("a"), _manifest("b"), _manifest("c")]
        order = loader._resolve_dependency_order()
        ids = [m.id for m in order]
        assert set(ids) == {"a", "b", "c"}
        # Order may be any topological order; with no deps, all are valid

    def test_single_dep(self) -> None:
        loader = Loader(extensions_dir=Path("."), data_dir=Path("."))
        loader._manifests = [_manifest("a", ["b"]), _manifest("b")]
        order = loader._resolve_dependency_order()
        ids = [m.id for m in order]
        assert ids.index("b") < ids.index("a")

    def test_chain(self) -> None:
        loader = Loader(extensions_dir=Path("."), data_dir=Path("."))
        loader._manifests = [
            _manifest("a", ["b"]),
            _manifest("b", ["c"]),
            _manifest("c"),
        ]
        order = loader._resolve_dependency_order()
        ids = [m.id for m in order]
        assert ids.index("c") < ids.index("b") < ids.index("a")

    def test_cycle_raises(self) -> None:
        loader = Loader(extensions_dir=Path("."), data_dir=Path("."))
        loader._manifests = [
            _manifest("a", ["b"]),
            _manifest("b", ["a"]),
        ]
        with pytest.raises(ValueError, match="Cycle in depends_on involving"):
            loader._resolve_dependency_order()

    def test_missing_dep_raises(self) -> None:
        loader = Loader(extensions_dir=Path("."), data_dir=Path("."))
        loader._manifests = [_manifest("a", ["missing"])]
        with pytest.raises(ValueError, match="depends on missing"):
            loader._resolve_dependency_order()


class TestDiscover:
    """Loader.discover() scans dir and loads manifests."""

    @pytest.mark.asyncio
    async def test_empty_dir(self, tmp_path: Path) -> None:
        loader = Loader(extensions_dir=tmp_path, data_dir=tmp_path)
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
        loader = Loader(extensions_dir=tmp_path, data_dir=tmp_path)
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
        loader = Loader(extensions_dir=tmp_path, data_dir=tmp_path)
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
        loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir)
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
        loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir)
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

        loader = Loader(extensions_dir=Path("."), data_dir=Path("."))
        loader._manifests = [_manifest("x")]
        loader._extensions = {"x": MockExt()}
        loader._state = {"x": ExtensionState.INACTIVE}
        router = MessageRouter()
        await loader.initialize_all(router)
        assert len(received_ctx) == 1
        assert getattr(received_ctx[0], "extension_id", None) == "x"
        assert getattr(received_ctx[0], "config", None) == {}

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

        loader = Loader(extensions_dir=Path("."), data_dir=Path("."))
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
        loader = Loader(extensions_dir=Path("."), data_dir=Path("."))
        loader._manifests = [_manifest("a", ["b"]), _manifest("b")]  # load order: b, a
        loader._extensions = {"a": a, "b": b}
        loader._state = {"a": ExtensionState.ACTIVE, "b": ExtensionState.ACTIVE}
        loader._service_tasks = {}
        loader._cron_task = None
        loader._health_task = None
        await loader.shutdown()
        # Shutdown order should be reverse of load order: a, then b
        assert order == ["a.stop", "a.destroy", "b.stop", "b.destroy"]
