"""Tests for deferred tool resolver and gateway tools."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents.deferred_tool_resolver import (
    DeferredToolResolver,
    ToolCatalogEntry,
    make_deferred_tool_tools,
)
from core.extensions.contract import AgentResponse
from core.extensions.loader import Loader
from core.extensions.manifest import ExtensionManifest
from core.extensions.contract import ExtensionState


class _DummyToolProvider:
    def get_tools(self) -> list[object]:
        return []


def _catalog() -> list[ToolCatalogEntry]:
    return [
        ToolCatalogEntry(
            tool_id="core_tools",
            name="Core Tools",
            description="File and patch operations",
            keywords=["file", "patch"],
        ),
        ToolCatalogEntry(
            tool_id="memory",
            name="Memory",
            description="Remember and search facts about user context",
            keywords=["memory", "remember", "recall", "facts"],
        ),
        ToolCatalogEntry(
            tool_id="scheduler",
            name="Scheduler",
            description="Create reminders and recurring jobs",
            keywords=["schedule", "reminder", "cron"],
        ),
    ]


class TestDeferredToolResolver:
    def test_resolve_selects_relevant_tool_with_core(self) -> None:
        resolver = DeferredToolResolver(catalog_getter=_catalog)
        result = resolver.resolve(
            task="Запомни предпочтения пользователя и найди это в memory",
            max_tools=3,
        )
        assert result.selected_tool_ids[0] == "core_tools"
        assert "memory" in result.selected_tool_ids

    def test_resolve_falls_back_to_core_tools(self) -> None:
        resolver = DeferredToolResolver(catalog_getter=_catalog)
        result = resolver.resolve(task="Скажи привет", max_tools=2)
        assert result.selected_tool_ids == ["core_tools"]


class TestDeferredToolGateway:
    @pytest.mark.asyncio
    async def test_run_with_resolved_tools_executes_dynamic_agent(self) -> None:
        from agents.tool_context import ToolContext

        factory = MagicMock()
        factory.create.return_value = "dyn_abc123"

        registry = MagicMock()
        registry.invoke = AsyncMock(
            return_value=AgentResponse(status="success", content="done")
        )

        tools = make_deferred_tool_tools(
            factory=factory,
            registry=registry,
            catalog_getter=_catalog,
        )
        run_tool = next(t for t in tools if getattr(t, "name", "") == "run_with_resolved_tools")

        args = json.dumps(
            {
                "task": "Сделай напоминание через scheduler",
                "context": "User asked for proactive reminder",
                "max_tools": 3,
            }
        )
        ctx = ToolContext(
            context=object(),
            tool_name=run_tool.name,
            tool_call_id="test-call-id",
            tool_arguments=args,
        )

        result = await run_tool.on_invoke_tool(ctx, args)
        assert result.success is True
        assert result.agent_id == "dyn_abc123"
        assert result.content == "done"
        assert "core_tools" in result.tool_ids
        assert "scheduler" in result.tool_ids
        factory.create.assert_called_once()
        registry.invoke.assert_awaited_once()


class TestLoaderToolCatalog:
    def test_get_tool_catalog_includes_core_and_tool_providers(self, tmp_path: Path) -> None:
        loader = Loader(extensions_dir=tmp_path, data_dir=tmp_path, settings={"extensions": {}})
        loader._extensions = {
            "memory": _DummyToolProvider(),
            "not_tool": object(),
        }
        loader._state = {
            "memory": ExtensionState.INACTIVE,
            "not_tool": ExtensionState.INACTIVE,
        }
        loader._manifests = [
            ExtensionManifest.model_validate(
                {
                    "id": "memory",
                    "name": "Memory",
                    "entrypoint": "main:Ext",
                    "description": "Stores and retrieves user memory",
                }
            ),
            ExtensionManifest.model_validate(
                {
                    "id": "not_tool",
                    "name": "NotTool",
                    "entrypoint": "main:Ext",
                }
            ),
        ]

        catalog = loader.get_tool_catalog()
        tool_ids = [entry["tool_id"] for entry in catalog]
        assert "core_tools" in tool_ids
        assert "memory" in tool_ids
        assert "not_tool" not in tool_ids
