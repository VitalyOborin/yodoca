"""Tests for AgentFactory, DynamicAgentProvider, TTL cleanup."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agents.factory import AgentFactory, AgentSpec, DynamicAgentProvider
from core.agents.registry import AgentRecord, AgentRegistry


def _make_mock_model_router() -> MagicMock:
    router = MagicMock()
    router.get_default_agent_config.return_value = {
        "provider": "openai",
        "model": "gpt-4",
    }
    router.get_model.return_value = "gpt-4"
    return router


class TestAgentSpec:
    def test_defaults(self) -> None:
        spec = AgentSpec(name="Test", instruction="Do X")
        assert spec.name == "Test"
        assert spec.instruction == "Do X"
        assert spec.description == ""
        assert spec.tools == []
        assert spec.model is None
        assert spec.max_turns == 25
        assert spec.ttl_seconds == 1800

    def test_explicit_description(self) -> None:
        spec = AgentSpec(
            name="Coder", instruction="Write code", description="Code specialist"
        )
        assert spec.description == "Code specialist"


class TestDynamicAgentProvider:
    @pytest.mark.asyncio
    async def test_invoke_success(self) -> None:
        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.final_output = "done"

        with patch(
            "core.agents.factory.Runner.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            provider = DynamicAgentProvider(
                name="Test",
                description="Test agent",
                agent=mock_agent,
                max_turns=5,
            )
            result = await provider.invoke("task")
        assert result.status == "success"
        assert result.content == "done"

    @pytest.mark.asyncio
    async def test_invoke_error(self) -> None:
        mock_agent = MagicMock()

        with patch(
            "core.agents.factory.Runner.run",
            new_callable=AsyncMock,
            side_effect=RuntimeError("oops"),
        ):
            provider = DynamicAgentProvider(
                name="Test",
                description="Test agent",
                agent=mock_agent,
                max_turns=5,
            )
            result = await provider.invoke("task")
        assert result.status == "error"
        assert result.error == "oops"

    @pytest.mark.asyncio
    async def test_invoke_prepends_context(self) -> None:
        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.final_output = "done"

        from core.extensions.contract import AgentInvocationContext

        ctx = AgentInvocationContext(conversation_summary="User wants X")
        with patch(
            "core.agents.factory.Runner.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_run:
            provider = DynamicAgentProvider(
                name="Test",
                description="Test agent",
                agent=mock_agent,
                max_turns=5,
            )
            await provider.invoke("do the thing", ctx)
        call_args = mock_run.call_args
        prompt = call_args[0][1]
        assert "Context:" in prompt
        assert "User wants X" in prompt
        assert "Task:" in prompt
        assert "do the thing" in prompt

    @pytest.mark.asyncio
    async def test_invoke_without_context(self) -> None:
        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.final_output = "done"

        with patch(
            "core.agents.factory.Runner.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_run:
            provider = DynamicAgentProvider(
                name="Test",
                description="Test agent",
                agent=mock_agent,
                max_turns=5,
            )
            await provider.invoke("do the thing")
        call_args = mock_run.call_args
        prompt = call_args[0][1]
        assert prompt == "do the thing"

    def test_get_agent_descriptor(self) -> None:
        provider = DynamicAgentProvider(
            name="Foo",
            description="Foo agent",
            agent=MagicMock(),
            integration_mode="tool",
        )
        d = provider.get_agent_descriptor()
        assert d.name == "Foo"
        assert d.description == "Foo agent"
        assert d.integration_mode == "tool"


class TestAgentFactory:
    def test_create_registers_agent(self) -> None:
        registry = AgentRegistry()
        router = _make_mock_model_router()
        tool_resolver = MagicMock(return_value=[])

        factory = AgentFactory(router, tool_resolver, registry)
        spec = AgentSpec(
            name="TestAgent",
            instruction="Execute task",
            tools=[],
            max_turns=10,
        )
        agent_id = factory.create(spec)
        assert agent_id.startswith("dyn_")
        assert len(agent_id) == 16

        pair = registry.get(agent_id)
        assert pair is not None
        record, provider = pair
        assert record.name == "TestAgent"
        assert record.source == "dynamic"
        assert record.expires_at is not None

    def test_create_uses_explicit_description(self) -> None:
        registry = AgentRegistry()
        router = _make_mock_model_router()
        tool_resolver = MagicMock(return_value=[])

        factory = AgentFactory(router, tool_resolver, registry)
        spec = AgentSpec(
            name="Coder",
            instruction="You are a code specialist with deep expertise...",
            description="Code generation agent",
        )
        agent_id = factory.create(spec)
        pair = registry.get(agent_id)
        assert pair is not None
        record, _ = pair
        assert record.description == "Code generation agent"

    def test_create_fallback_description_from_instruction(self) -> None:
        registry = AgentRegistry()
        router = _make_mock_model_router()
        tool_resolver = MagicMock(return_value=[])

        factory = AgentFactory(router, tool_resolver, registry)
        spec = AgentSpec(name="Worker", instruction="Short task")
        agent_id = factory.create(spec)
        pair = registry.get(agent_id)
        assert pair is not None
        record, _ = pair
        assert record.description == "Short task"

    def test_create_with_model_override(self) -> None:
        registry = AgentRegistry()
        router = _make_mock_model_router()
        tool_resolver = MagicMock(return_value=[])

        factory = AgentFactory(router, tool_resolver, registry)
        spec = AgentSpec(
            name="CodeAgent",
            instruction="Write code",
            model="gpt-5.2-codex",
        )
        agent_id = factory.create(spec)
        router.register_agent_config.assert_called_once()
        call_args = router.register_agent_config.call_args[0]
        assert call_args[0] == agent_id
        assert call_args[1]["model"] == "gpt-5.2-codex"


class TestAgentRegistryCleanup:
    def test_cleanup_expired_removes_dynamic_agents(self) -> None:
        registry = AgentRegistry()
        mock_provider = MagicMock()
        past = datetime.now(UTC) - timedelta(minutes=5)
        record = AgentRecord(
            id="dyn_expired",
            name="Expired",
            description="Expired agent",
            source="dynamic",
            expires_at=past,
        )
        registry.register(record, mock_provider)
        assert registry.get("dyn_expired") is not None

        removed = registry.cleanup_expired()
        assert removed == 1
        assert registry.get("dyn_expired") is None

    def test_unregister_calls_on_unregister_callback(self) -> None:
        callback = MagicMock()
        registry = AgentRegistry(on_unregister=callback)
        mock_provider = MagicMock()
        past = datetime.now(UTC) - timedelta(minutes=5)
        record = AgentRecord(
            id="dyn_test",
            name="Test",
            description="Test",
            source="dynamic",
            expires_at=past,
        )
        registry.register(record, mock_provider)
        registry.cleanup_expired()
        callback.assert_called_once_with("dyn_test")

    def test_cleanup_expired_keeps_static_agents(self) -> None:
        registry = AgentRegistry()
        mock_provider = MagicMock()
        record = AgentRecord(
            id="static_agent",
            name="Static",
            description="Static agent",
            source="static",
            expires_at=None,
        )
        registry.register(record, mock_provider)
        removed = registry.cleanup_expired()
        assert removed == 0
        assert registry.get("static_agent") is not None


class TestDelegationToolsCreateAgent:
    """Tests for create_agent and list_available_tools in delegation_tools."""

    def test_make_delegation_tools_with_factory_returns_four_tools(self) -> None:
        from core.agents.delegation_tools import make_delegation_tools

        registry = AgentRegistry()
        router = _make_mock_model_router()
        tool_resolver = MagicMock(return_value=[])
        factory = AgentFactory(router, tool_resolver, registry)
        tools = make_delegation_tools(registry, factory, lambda: ["core_tools"])
        assert len(tools) == 4
        names = [t.name for t in tools]
        assert "list_agents" in names
        assert "delegate_task" in names
        assert "create_agent" in names
        assert "list_available_tools" in names

    def test_make_delegation_tools_without_factory_returns_two_tools(
        self,
    ) -> None:
        from core.agents.delegation_tools import make_delegation_tools

        registry = AgentRegistry()
        tools = make_delegation_tools(registry)
        assert len(tools) == 2
        names = [t.name for t in tools]
        assert "list_agents" in names
        assert "delegate_task" in names

    def test_make_delegation_tools_with_tool_ids_getter_includes_list_tools(
        self,
    ) -> None:
        from core.agents.delegation_tools import make_delegation_tools

        registry = AgentRegistry()
        tools = make_delegation_tools(
            registry, None, lambda: ["core_tools", "web_search"]
        )
        assert len(tools) == 3
        names = [t.name for t in tools]
        assert "list_available_tools" in names

    def test_make_delegation_tools_with_catalog_includes_list_models(
        self,
    ) -> None:
        from core.agents.delegation_tools import make_delegation_tools
        from core.llm.catalog import ModelCatalog

        registry = AgentRegistry()
        catalog = ModelCatalog()
        tools = make_delegation_tools(registry, catalog=catalog)
        assert len(tools) == 3
        names = [t.name for t in tools]
        assert "list_agents" in names
        assert "delegate_task" in names
        assert "list_models" in names

    @pytest.mark.asyncio
    async def test_list_agents_with_catalog_returns_enriched_info(self) -> None:
        import json

        from agents.tool_context import ToolContext

        from core.agents.delegation_tools import make_delegation_tools
        from core.llm.catalog import ModelCatalog

        registry = AgentRegistry()
        catalog = ModelCatalog()
        record = AgentRecord(
            id="test_agent",
            name="Test",
            description="Test agent",
            model="gpt-5-mini",
            tools=["kv"],
            source="static",
        )
        registry.register(record, MagicMock())
        tools = make_delegation_tools(registry, catalog=catalog)
        list_agents_tool = next(t for t in tools if t.name == "list_agents")
        args = json.dumps({"available_only": False})
        ctx = ToolContext(
            context=object(),
            tool_name="list_agents",
            tool_call_id="test-call-id",
            tool_arguments=args,
        )
        result = await list_agents_tool.on_invoke_tool(ctx, args)
        assert len(result.agents) == 1
        agent = result.agents[0]
        assert agent.id == "test_agent"
        assert agent.cost_tier == "medium"
        assert agent.capability_tier == "standard"
        assert agent.strengths == []
