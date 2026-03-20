"""Tests for KV extension tools: kv_set, kv_get structured results."""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from core.extensions.loader import Loader
from core.extensions.routing.router import MessageRouter
from core.settings_models import AppSettings


def _make_tool_ctx(tool_name: str, tool_arguments: str):
    from agents.tool_context import ToolContext

    return ToolContext(
        context=object(),
        tool_name=tool_name,
        tool_call_id="test-call-id",
        tool_arguments=tool_arguments,
    )


async def _init_kv_extension(
    tmp_path: Path,
    settings: AppSettings | None = None,
):
    """Load sandbox extensions and return initialized kv extension instance."""
    project_root = Path(__file__).resolve().parent.parent
    extensions_dir = project_root / "sandbox" / "extensions"
    data_dir = tmp_path / "data"
    if not extensions_dir.exists():
        pytest.skip("sandbox/extensions not found")
    loader = Loader(
        extensions_dir=extensions_dir,
        data_dir=data_dir,
        settings=settings or AppSettings(),
    )
    await loader.discover()
    await loader.load_all()
    router = MessageRouter()
    await loader.initialize_all(router)
    kv_ext = loader._extensions.get("kv")
    if not kv_ext:
        pytest.skip("kv extension not loaded")
    return kv_ext, data_dir


@pytest.mark.asyncio
async def test_kv_set_returns_structured_result(tmp_path: Path) -> None:
    """kv_set returns KvSetResult with success, key, status."""
    project_root = Path(__file__).resolve().parent.parent
    extensions_dir = project_root / "sandbox" / "extensions"
    data_dir = tmp_path / "data"
    if not extensions_dir.exists():
        pytest.skip("sandbox/extensions not found")
    loader = Loader(
        extensions_dir=extensions_dir,
        data_dir=data_dir,
        settings=AppSettings(),
    )
    await loader.discover()
    await loader.load_all()
    router = MessageRouter()
    await loader.initialize_all(router)
    kv_ext = loader._extensions.get("kv")
    if not kv_ext:
        pytest.skip("kv extension not loaded")
    tools = kv_ext.get_tools()
    kv_set = next(t for t in tools if getattr(t, "name", None) == "kv_set")
    args = json.dumps({"key": "test_key", "value": "test_value"}, ensure_ascii=False)
    result = await kv_set.on_invoke_tool(_make_tool_ctx(kv_set.name, args), args)
    assert result.success is True
    assert result.key == "test_key"
    assert result.status == "set"
    assert result.error is None


@pytest.mark.asyncio
async def test_kv_get_exact_match_returns_structured_result(tmp_path: Path) -> None:
    """kv_get returns KvGetResult with value for exact match."""
    project_root = Path(__file__).resolve().parent.parent
    extensions_dir = project_root / "sandbox" / "extensions"
    data_dir = tmp_path / "data"
    if not extensions_dir.exists():
        pytest.skip("sandbox/extensions not found")
    loader = Loader(
        extensions_dir=extensions_dir,
        data_dir=data_dir,
        settings=AppSettings(),
    )
    await loader.discover()
    await loader.load_all()
    router = MessageRouter()
    await loader.initialize_all(router)
    kv_ext = loader._extensions.get("kv")
    if not kv_ext:
        pytest.skip("kv extension not loaded")
    await kv_ext.set("my_key", "my_value")
    tools = kv_ext.get_tools()
    kv_get = next(t for t in tools if getattr(t, "name", None) == "kv_get")
    args = json.dumps({"key": "my_key"}, ensure_ascii=False)
    result = await kv_get.on_invoke_tool(_make_tool_ctx(kv_get.name, args), args)
    assert result.success is True
    assert result.key == "my_key"
    assert result.value == "my_value"
    assert result.status == "found"
    assert result.matches == {}


@pytest.mark.asyncio
async def test_kv_get_not_found_returns_structured_result(tmp_path: Path) -> None:
    """kv_get returns KvGetResult with success=False for missing key."""
    project_root = Path(__file__).resolve().parent.parent
    extensions_dir = project_root / "sandbox" / "extensions"
    data_dir = tmp_path / "data"
    if not extensions_dir.exists():
        pytest.skip("sandbox/extensions not found")
    loader = Loader(
        extensions_dir=extensions_dir,
        data_dir=data_dir,
        settings=AppSettings(),
    )
    await loader.discover()
    await loader.load_all()
    router = MessageRouter()
    await loader.initialize_all(router)
    kv_ext = loader._extensions.get("kv")
    if not kv_ext:
        pytest.skip("kv extension not loaded")
    tools = kv_ext.get_tools()
    kv_get = next(t for t in tools if getattr(t, "name", None) == "kv_get")
    args = json.dumps({"key": "nonexistent_key"}, ensure_ascii=False)
    result = await kv_get.on_invoke_tool(_make_tool_ctx(kv_get.name, args), args)
    assert result.success is False
    assert result.key == "nonexistent_key"
    assert result.status == "not_found"
    assert result.error is not None


@pytest.mark.asyncio
async def test_kv_concurrent_sets_persist_all_keys(tmp_path: Path) -> None:
    """Parallel kv_set calls must not lose writes (serialized under lock)."""
    kv_ext, data_dir = await _init_kv_extension(tmp_path)
    tools = kv_ext.get_tools()
    kv_set = next(t for t in tools if getattr(t, "name", None) == "kv_set")
    n = 30

    async def _set(i: int) -> None:
        args = json.dumps(
            {"key": f"ckey_{i}", "value": str(i)},
            ensure_ascii=False,
        )
        result = await kv_set.on_invoke_tool(_make_tool_ctx(kv_set.name, args), args)
        assert result.success is True, result.error

    await asyncio.gather(*(_set(i) for i in range(n)))
    path = data_dir / "kv" / "values.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == n
    for i in range(n):
        assert data[f"ckey_{i}"] == str(i)


@pytest.mark.asyncio
async def test_kv_max_entries_blocks_new_key(tmp_path: Path) -> None:
    """When store is at max_entries, kv_set returns error for a new key."""
    settings = AppSettings(extensions={"kv": {"max_entries": 3}})
    kv_ext, _data_dir = await _init_kv_extension(tmp_path, settings=settings)
    tools = kv_ext.get_tools()
    kv_set = next(t for t in tools if getattr(t, "name", None) == "kv_set")

    for i in range(3):
        args = json.dumps({"key": f"lim_{i}", "value": "x"}, ensure_ascii=False)
        result = await kv_set.on_invoke_tool(_make_tool_ctx(kv_set.name, args), args)
        assert result.success is True, result.error

    args = json.dumps({"key": "lim_overflow", "value": "y"}, ensure_ascii=False)
    result = await kv_set.on_invoke_tool(_make_tool_ctx(kv_set.name, args), args)
    assert result.success is False
    assert result.status == "error"
    assert result.error is not None
    assert "limit reached" in result.error.lower()


@pytest.mark.asyncio
async def test_kv_corrupt_file_logs_and_reads_empty(tmp_path: Path, caplog) -> None:
    """Corrupt values.json yields empty reads and a warning log."""
    kv_ext, data_dir = await _init_kv_extension(tmp_path)
    values_path = data_dir / "kv" / "values.json"
    values_path.write_text("{ not valid json", encoding="utf-8")

    tools = kv_ext.get_tools()
    kv_get = next(t for t in tools if getattr(t, "name", None) == "kv_get")
    with caplog.at_level(logging.WARNING):
        args = json.dumps({"key": "any_key"}, ensure_ascii=False)
        result = await kv_get.on_invoke_tool(_make_tool_ctx(kv_get.name, args), args)

    assert result.success is False
    assert result.status == "not_found"
    assert any("corrupt" in r.message.lower() for r in caplog.records)
