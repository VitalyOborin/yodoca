"""Tests for KV extension tools: kv_set, kv_get structured results."""

import json
from pathlib import Path

import pytest

from core.extensions.loader import Loader
from core.extensions.router import MessageRouter


def _make_tool_ctx(tool_name: str, tool_arguments: str):
    from agents.tool_context import ToolContext

    return ToolContext(
        context=object(),
        tool_name=tool_name,
        tool_call_id="test-call-id",
        tool_arguments=tool_arguments,
    )


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
        settings={"extensions": {}},
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
        settings={"extensions": {}},
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
        settings={"extensions": {}},
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
