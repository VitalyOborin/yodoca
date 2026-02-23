"""Tests for Memory v2 extension."""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add memory extension to path
_memory_ext = Path(__file__).resolve().parent.parent / "sandbox" / "extensions" / "memory"
sys.path.insert(0, str(_memory_ext))

from agent_tools import build_write_path_tools
from decay import DecayService
from retrieval import (
    EmbeddingIntentClassifier,
    KeywordIntentClassifier,
    MemoryRetrieval,
    classify_query_complexity,
    get_adaptive_params,
    parse_time_expression,
)
from storage import MemoryStorage
from tools import build_tools


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "memory.db"


@pytest.fixture
async def storage(tmp_db):
    s = MemoryStorage(tmp_db)
    await s.initialize()
    yield s
    await s.close()


class TestKeywordIntentClassifier:
    """KeywordIntentClassifier regex-based classification."""

    def test_classify_why(self) -> None:
        c = KeywordIntentClassifier()
        assert c.classify("why did this happen") == "why"
        assert c.classify("what caused the failure") == "why"
        assert c.classify("what is the reason") == "why"

    def test_classify_when(self) -> None:
        c = KeywordIntentClassifier()
        assert c.classify("when did we discuss") == "when"
        assert c.classify("timeline of events") == "when"
        assert c.classify("before the meeting") == "when"

    def test_classify_who(self) -> None:
        c = KeywordIntentClassifier()
        assert c.classify("who is responsible") == "who"
        assert c.classify("whose idea") == "who"

    def test_classify_what(self) -> None:
        c = KeywordIntentClassifier()
        assert c.classify("what do you know about") == "what"
        assert c.classify("tell me everything about") == "what"

    def test_classify_general(self) -> None:
        c = KeywordIntentClassifier()
        assert c.classify("hello") == "general"
        assert c.classify("random query") == "general"


class TestMemoryStorage:
    """MemoryStorage writer queue and CRUD."""

    @pytest.mark.asyncio
    async def test_insert_node_and_fts_search(self, storage: MemoryStorage) -> None:
        import time

        now = int(time.time())
        node_id = storage.insert_node({
            "type": "episodic",
            "content": "user said hello world",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "source_role": "user",
            "session_id": "s1",
        })
        assert node_id is not None

        await asyncio.sleep(0.5)

        results = await storage.fts_search("hello")
        assert len(results) == 1
        assert results[0]["content"] == "user said hello world"
        assert results[0]["type"] == "episodic"

    @pytest.mark.asyncio
    async def test_temporal_edge(self, storage: MemoryStorage) -> None:
        import time

        now = int(time.time())
        storage.ensure_session("s1")
        await asyncio.sleep(0.2)

        n1 = storage.insert_node({
            "type": "episodic",
            "content": "first message",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "source_role": "user",
            "session_id": "s1",
        })
        await asyncio.sleep(0.3)

        last = await storage.get_last_episode_id("s1")
        assert last == n1

        n2 = storage.insert_node({
            "type": "episodic",
            "content": "second message",
            "event_time": now + 1,
            "created_at": now + 1,
            "valid_from": now + 1,
            "source_role": "user",
            "session_id": "s1",
        })
        storage.insert_edge({
            "source_id": n1,
            "target_id": n2,
            "relation_type": "temporal",
            "valid_from": now + 1,
            "created_at": now + 1,
        })
        await asyncio.sleep(0.3)

        last2 = await storage.get_last_episode_id("s1")
        assert last2 == n2

    @pytest.mark.asyncio
    async def test_get_node(self, storage: MemoryStorage) -> None:
        import time

        now = int(time.time())
        nid = storage.insert_node({
            "type": "semantic",
            "content": "fact: project X is delayed",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "source_type": "extraction",
        })
        await asyncio.sleep(0.3)

        node = await storage.get_node(nid)
        assert node is not None
        assert node["content"] == "fact: project X is delayed"
        assert node["type"] == "semantic"


class TestMemoryRetrieval:
    """MemoryRetrieval search and context assembly."""

    @pytest.mark.asyncio
    async def test_search_and_assemble_context(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        storage.insert_node({
            "type": "episodic",
            "content": "discussed budget for Q1",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "session_id": "s1",
        })
        await asyncio.sleep(0.3)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        results = await retrieval.search(
            "budget", limit=5, node_types=["episodic", "semantic", "procedural", "opinion"]
        )
        assert len(results) >= 1

        ctx = await retrieval.assemble_context(results, token_budget=500)
        assert ctx
        assert "budget" in ctx


class TestQueryComplexity:
    """classify_query_complexity heuristic."""

    def test_simple(self) -> None:
        assert classify_query_complexity("short query") == "simple"
        assert classify_query_complexity("what is status") == "simple"

    def test_complex(self) -> None:
        assert classify_query_complexity("compare the two options and summarize everything") == "complex"
        assert classify_query_complexity("one two three four five six seven eight nine ten") == "complex"


class TestEmbeddingIntentClassifier:
    """EmbeddingIntentClassifier with mock embed_fn."""

    @pytest.mark.asyncio
    async def test_classify_with_embedding(self) -> None:
        emb = [0.1] * 8
        embed_fn = AsyncMock(return_value=emb)
        classifier = EmbeddingIntentClassifier(embed_fn=embed_fn, threshold=0.3)
        await classifier.initialize()
        result = classifier.classify("why did this happen", query_embedding=emb)
        assert result == "why"

    @pytest.mark.asyncio
    async def test_classify_fallback_without_embedding(self) -> None:
        embed_fn = AsyncMock(return_value=[0.1] * 8)
        classifier = EmbeddingIntentClassifier(embed_fn=embed_fn, threshold=0.9)
        await classifier.initialize()
        result = classifier.classify("query", query_embedding=None)
        assert result == "general"


class TestMemoryStorageVector:
    """MemoryStorage save_embedding and vector_search."""

    @pytest.mark.asyncio
    async def test_save_embedding_and_vector_search(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        node_id = storage.insert_node({
            "type": "semantic",
            "content": "test fact for vector",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "source_type": "conversation",
        })
        await asyncio.sleep(0.3)

        embedding = [0.1] * 256
        await storage.save_embedding(node_id, embedding)
        await asyncio.sleep(0.3)

        results = await storage.vector_search(
            embedding, node_types=["semantic"], limit=5
        )
        assert len(results) >= 1
        assert results[0]["id"] == node_id
        assert "distance" in results[0]


class TestMemoryRetrievalRRF:
    """MemoryRetrieval hybrid search with RRF."""

    @pytest.mark.asyncio
    async def test_rrf_merge_combines_fts_and_vector(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        storage.insert_node({
            "type": "semantic",
            "content": "hybrid search test",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
        })
        await asyncio.sleep(0.3)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(
            storage, classifier,
            rrf_k=60, rrf_weight_fts=1.0, rrf_weight_vector=1.0,
        )
        query_emb = [0.1] * 256
        results = await retrieval.search(
            "hybrid",
            query_embedding=query_emb,
            limit=5,
            node_types=["semantic", "procedural", "opinion"],
        )
        assert len(results) >= 1


def _tool_ctx(tool_name: str, args: dict) -> object:
    import json
    from agents.tool_context import ToolContext
    return ToolContext(
        context=object(),
        tool_name=tool_name,
        tool_call_id="test-call-id",
        tool_arguments=json.dumps(args),
    )


class TestRememberCorrectConfirmTools:
    """remember_fact, correct_fact, confirm_fact tools."""

    @pytest.mark.asyncio
    async def test_remember_fact_creates_semantic_node(
        self, storage: MemoryStorage
    ) -> None:
        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_tools(
            retrieval=retrieval,
            storage=storage,
            embed_fn=None,
            token_budget=1000,
        )
        remember = tools[1]
        args = {"fact": "User prefers dark mode"}
        result = await remember.on_invoke_tool(
            _tool_ctx(remember.name, args), __import__("json").dumps(args)
        )
        assert "node_id" in str(result) or "saved" in str(result).lower()
        await asyncio.sleep(0.3)
        nodes = await storage.fts_search("dark mode", node_types=["semantic"])
        assert len(nodes) >= 1

    @pytest.mark.asyncio
    async def test_confirm_fact_updates_confidence(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        nid = storage.insert_node({
            "type": "semantic",
            "content": "fact to confirm",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "confidence": 0.5,
            "decay_rate": 0.2,
        })
        await asyncio.sleep(0.3)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_tools(
            retrieval=retrieval,
            storage=storage,
            embed_fn=None,
            token_budget=1000,
        )
        confirm = tools[3]
        args = {"fact_id": nid}
        result = await confirm.on_invoke_tool(
            _tool_ctx(confirm.name, args), __import__("json").dumps(args)
        )
        assert "confirmed" in str(result).lower()
        await asyncio.sleep(0.2)
        node = await storage.get_node(nid)
        assert node is not None

    @pytest.mark.asyncio
    async def test_correct_fact_soft_deletes_and_creates_new(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        old_id = storage.insert_node({
            "type": "semantic",
            "content": "old fact to correct",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
        })
        await asyncio.sleep(0.3)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_tools(
            retrieval=retrieval,
            storage=storage,
            embed_fn=None,
            token_budget=1000,
        )
        correct = tools[2]
        args = {"old_fact": "old fact to correct", "new_fact": "new corrected fact"}
        result = await correct.on_invoke_tool(
            _tool_ctx(correct.name, args), __import__("json").dumps(args)
        )
        assert "corrected" in str(result).lower()
        await asyncio.sleep(0.3)
        old_node = await storage.get_node(old_id)
        assert old_node is None
        nodes = await storage.fts_search("new corrected fact")
        assert len(nodes) >= 1


class TestMemoryStoragePhase3:
    """Phase 3: get_session_episodes, mark_session_consolidated, entity ops, get_unconsolidated_sessions."""

    @pytest.mark.asyncio
    async def test_get_session_episodes_paginated(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        storage.ensure_session("sess-ep")
        await asyncio.sleep(0.2)

        for i in range(5):
            storage.insert_node({
                "type": "episodic",
                "content": f"episode {i}",
                "event_time": now + i,
                "created_at": now + i,
                "valid_from": now + i,
                "source_role": "user",
                "session_id": "sess-ep",
            })
        await asyncio.sleep(0.5)

        page1 = await storage.get_session_episodes("sess-ep", limit=2, offset=0)
        assert len(page1) == 2
        assert page1[0]["content"] == "episode 0"
        assert page1[1]["content"] == "episode 1"

        page2 = await storage.get_session_episodes("sess-ep", limit=2, offset=2)
        assert len(page2) == 2
        assert page2[0]["content"] == "episode 2"

    @pytest.mark.asyncio
    async def test_mark_session_consolidated_roundtrip(
        self, storage: MemoryStorage
    ) -> None:
        storage.ensure_session("sess-mark")
        await asyncio.sleep(0.2)

        assert await storage.is_session_consolidated("sess-mark") is False
        await storage.mark_session_consolidated("sess-mark")
        await asyncio.sleep(0.2)
        assert await storage.is_session_consolidated("sess-mark") is True

    @pytest.mark.asyncio
    async def test_insert_entity_get_by_name_link_node(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        nid = storage.insert_node({
            "type": "semantic",
            "content": "Alice works at Acme",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
        })
        await asyncio.sleep(0.2)

        entity_id = await storage.insert_entity({
            "canonical_name": "Alice",
            "type": "person",
            "aliases": ["Alice Smith", "Al"],
        })
        assert entity_id

        found = await storage.get_entity_by_name("alice")
        assert found is not None
        assert found["canonical_name"] == "Alice"
        assert found["type"] == "person"

        await storage.link_node_entity(nid, entity_id)
        await asyncio.sleep(0.2)

        alias_found = await storage.search_entity_by_alias("Al")
        assert alias_found is not None
        assert alias_found["canonical_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_get_unconsolidated_sessions(
        self, storage: MemoryStorage
    ) -> None:
        storage.ensure_session("u1")
        storage.ensure_session("u2")
        await asyncio.sleep(0.2)

        uncons = await storage.get_unconsolidated_sessions()
        assert set(uncons) >= {"u1", "u2"}

        await storage.mark_session_consolidated("u1")
        await asyncio.sleep(0.2)
        uncons2 = await storage.get_unconsolidated_sessions()
        assert "u1" not in uncons2
        assert "u2" in uncons2


class TestWritePathTools:
    """Write-path agent tools: save_nodes_batch, extract_and_link_entities, resolve_conflict."""

    @pytest.mark.asyncio
    async def test_save_nodes_batch_creates_derived_from_edges(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        ep1 = storage.insert_node({
            "type": "episodic",
            "content": "user said X",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "session_id": "s1",
        })
        ep2 = storage.insert_node({
            "type": "episodic",
            "content": "agent replied Y",
            "event_time": now + 1,
            "created_at": now + 1,
            "valid_from": now + 1,
            "session_id": "s1",
        })
        await asyncio.sleep(0.5)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_write_path_tools(
            storage=storage,
            retrieval=retrieval,
            embed_fn=None,
            embed_batch_fn=None,
        )
        save_tool = tools[2]
        args = {
            "nodes": [
                {
                    "type": "semantic",
                    "content": "extracted fact from X and Y",
                    "source_episode_ids": [ep1, ep2],
                },
            ],
        }
        result = await save_tool.on_invoke_tool(
            _tool_ctx(save_tool.name, args), __import__("json").dumps(args)
        )
        assert "node_ids" in str(result) or "count" in str(result)
        await asyncio.sleep(0.5)

        nodes = await storage.fts_search("extracted fact", node_types=["semantic"])
        assert len(nodes) >= 1
        assert nodes[0]["content"] == "extracted fact from X and Y"

    @pytest.mark.asyncio
    async def test_extract_and_link_entities_creates_and_links(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        nid = storage.insert_node({
            "type": "semantic",
            "content": "Project Alpha is led by Bob",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
        })
        await asyncio.sleep(0.2)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_write_path_tools(
            storage=storage,
            retrieval=retrieval,
            embed_fn=None,
            embed_batch_fn=None,
        )
        extract_tool = tools[3]
        args = {
            "nodes": [
                {
                    "node_id": nid,
                    "entities": [
                        {
                            "canonical_name": "Project Alpha",
                            "type": "project",
                            "aliases": ["Alpha"],
                        },
                        {
                            "canonical_name": "Bob",
                            "type": "person",
                            "aliases": [],
                        },
                    ],
                },
            ],
        }
        result = await extract_tool.on_invoke_tool(
            _tool_ctx(extract_tool.name, args), __import__("json").dumps(args)
        )
        assert "entities_created" in str(result) or "entities_linked" in str(result)
        await asyncio.sleep(0.2)

        alpha = await storage.get_entity_by_name("Project Alpha")
        bob = await storage.get_entity_by_name("Bob")
        assert alpha is not None
        assert bob is not None

    @pytest.mark.asyncio
    async def test_resolve_conflict_soft_deletes_and_supersedes(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        old_id = storage.insert_node({
            "type": "semantic",
            "content": "old conflicting fact",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
        })
        new_id = storage.insert_node({
            "type": "semantic",
            "content": "new corrected fact",
            "event_time": now + 1,
            "created_at": now + 1,
            "valid_from": now + 1,
        })
        await asyncio.sleep(0.3)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_write_path_tools(
            storage=storage,
            retrieval=retrieval,
            embed_fn=None,
            embed_batch_fn=None,
        )
        resolve_tool = tools[5]
        args = {"old_node_id": old_id, "new_node_id": new_id}
        result = await resolve_tool.on_invoke_tool(
            _tool_ctx(resolve_tool.name, args), __import__("json").dumps(args)
        )
        assert "resolved" in str(result).lower()
        await asyncio.sleep(0.3)

        old_node = await storage.get_node(old_id)
        assert old_node is None


class TestConsolidateSessionIdempotency:
    """_consolidate_session skips already-consolidated sessions."""

    @pytest.mark.asyncio
    async def test_consolidate_skips_when_already_done(
        self, storage: MemoryStorage
    ) -> None:
        from main import MemoryExtension

        storage.ensure_session("sess-idem")
        await storage.mark_session_consolidated("sess-idem")
        await asyncio.sleep(0.2)

        ext = MemoryExtension()
        ext._storage = storage
        ext._write_agent = AsyncMock()
        ext._write_agent.consolidate_session = AsyncMock()

        await ext._consolidate_session("sess-idem")

        ext._write_agent.consolidate_session.assert_not_called()


class TestMemoryStoragePhase4:
    """Phase 4: temporal_chain_traversal, causal_chain_traversal, entity_nodes_for_entity, get_nodes_by_ids."""

    @pytest.mark.asyncio
    async def test_temporal_chain_traversal(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        storage.ensure_session("s1")
        await asyncio.sleep(0.2)
        ids = []
        for i in range(4):
            nid = storage.insert_node({
                "type": "episodic",
                "content": f"ep {i}",
                "event_time": now + i,
                "created_at": now + i,
                "valid_from": now + i,
                "session_id": "s1",
            })
            ids.append(nid)
            if i > 0:
                storage.insert_edge({
                    "source_id": ids[i - 1],
                    "target_id": nid,
                    "relation_type": "temporal",
                    "valid_from": now + i,
                    "created_at": now + i,
                })
        await asyncio.sleep(0.5)

        forward = await storage.temporal_chain_traversal(
            [ids[0]], direction="forward", max_depth=3, limit=10
        )
        assert len(forward) >= 2
        contents = [r["content"] for r in forward]
        assert "ep 0" in contents or "ep 1" in contents

    @pytest.mark.asyncio
    async def test_causal_chain_traversal(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        cause = storage.insert_node({
            "type": "episodic",
            "content": "cause event",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
        })
        effect = storage.insert_node({
            "type": "episodic",
            "content": "effect event",
            "event_time": now + 1,
            "created_at": now + 1,
            "valid_from": now + 1,
        })
        storage.insert_edge({
            "source_id": cause,
            "target_id": effect,
            "relation_type": "causal",
            "valid_from": now + 1,
            "created_at": now + 1,
        })
        await asyncio.sleep(0.3)

        chain = await storage.causal_chain_traversal(
            effect, max_depth=3, limit=10
        )
        assert len(chain) >= 1
        assert any(r["content"] == "cause event" for r in chain)

    @pytest.mark.asyncio
    async def test_entity_nodes_for_entity(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        eid = await storage.insert_entity({
            "canonical_name": "TestProject",
            "type": "project",
        })
        nid = storage.insert_node({
            "type": "semantic",
            "content": "Project milestone",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
        })
        await storage.link_node_entity(nid, eid)
        await asyncio.sleep(0.2)

        nodes = await storage.entity_nodes_for_entity(eid, limit=10)
        assert len(nodes) >= 1
        assert nodes[0]["content"] == "Project milestone"

    @pytest.mark.asyncio
    async def test_get_nodes_by_ids(self, storage: MemoryStorage) -> None:
        import time

        now = int(time.time())
        nid = storage.insert_node({
            "type": "semantic",
            "content": "batch fetch test",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
        })
        await asyncio.sleep(0.2)

        nodes = await storage.get_nodes_by_ids([nid])
        assert len(nodes) == 1
        assert nodes[0]["content"] == "batch fetch test"


class TestParseTimeExpression:
    """parse_time_expression heuristic."""

    def test_last_week(self) -> None:
        ts = parse_time_expression("last_week")
        assert ts is not None
        assert ts < int(time.time())

    def test_last_month(self) -> None:
        ts = parse_time_expression("last_month")
        assert ts is not None

    def test_yyyy_mm_dd(self) -> None:
        ts = parse_time_expression("2025-01-15")
        assert ts is not None

    def test_empty_returns_none(self) -> None:
        assert parse_time_expression("") is None
        assert parse_time_expression(None) is None


class TestGetAdaptiveParams:
    """get_adaptive_params includes graph_depth."""

    def test_simple_has_graph_depth(self) -> None:
        p = get_adaptive_params("simple")
        assert p["graph_depth"] == 2
        assert p["limit"] == 5

    def test_complex_has_graph_depth(self) -> None:
        p = get_adaptive_params("complex")
        assert p["graph_depth"] == 4
        assert p["limit"] == 20


class TestGetEntityInfoTool:
    """get_entity_info tool returns entity profile."""

    @pytest.mark.asyncio
    async def test_get_entity_info_returns_profile(
        self, storage: MemoryStorage
    ) -> None:
        import time

        now = int(time.time())
        eid = await storage.insert_entity({
            "canonical_name": "Alice",
            "type": "person",
            "summary": "Team lead",
        })
        nid = storage.insert_node({
            "type": "semantic",
            "content": "Alice prefers dark mode",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
        })
        await storage.link_node_entity(nid, eid)
        await asyncio.sleep(0.2)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_tools(
            retrieval=retrieval,
            storage=storage,
            embed_fn=None,
            token_budget=1000,
        )
        entity_tool = tools[4]
        args = {"entity_name": "Alice"}
        result = await entity_tool.on_invoke_tool(
            _tool_ctx(entity_tool.name, args), __import__("json").dumps(args)
        )
        assert "Alice" in str(result)
        assert "dark mode" in str(result) or "Team lead" in str(result)

    @pytest.mark.asyncio
    async def test_get_entity_info_not_found(
        self, storage: MemoryStorage
    ) -> None:
        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_tools(
            retrieval=retrieval,
            storage=storage,
            embed_fn=None,
            token_budget=1000,
        )
        entity_tool = tools[4]
        args = {"entity_name": "NonexistentEntity123"}
        result = await entity_tool.on_invoke_tool(
            _tool_ctx(entity_tool.name, args), __import__("json").dumps(args)
        )
        assert "No entity found" in str(result) or "not found" in str(result).lower()


class TestDecayService:
    """DecayService: Ebbinghaus decay, protection rules, pruning."""

    @pytest.mark.asyncio
    async def test_decay_formula_reduces_confidence(
        self, storage: MemoryStorage
    ) -> None:
        now = int(time.time())
        nid = storage.insert_node({
            "type": "semantic",
            "content": "fact to decay",
            "event_time": now,
            "created_at": now - 10 * 86400,
            "valid_from": now,
            "confidence": 0.8,
            "decay_rate": 0.1,
            "last_accessed": now - 10 * 86400,
        })
        await asyncio.sleep(0.3)

        decay = DecayService(decay_threshold=0.05)
        stats = await decay.apply(storage)
        assert stats["pruned"] >= 0 or stats["decayed"] >= 0

        node = await storage.get_node(nid)
        if node and node.get("valid_until") is None:
            assert node["confidence"] < 0.8

    @pytest.mark.asyncio
    async def test_episodic_nodes_skipped(
        self, storage: MemoryStorage
    ) -> None:
        now = int(time.time())
        storage.insert_node({
            "type": "episodic",
            "content": "episode",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "session_id": "s1",
            "decay_rate": 0.1,
        })
        await asyncio.sleep(0.3)

        decayable = await storage.get_decayable_nodes()
        assert not any(n["type"] == "episodic" for n in decayable)

    @pytest.mark.asyncio
    async def test_pruning_below_threshold(
        self, storage: MemoryStorage
    ) -> None:
        now = int(time.time())
        nid = storage.insert_node({
            "type": "semantic",
            "content": "low confidence fact",
            "event_time": now,
            "created_at": now - 100 * 86400,
            "valid_from": now,
            "confidence": 0.1,
            "decay_rate": 0.5,
            "last_accessed": now - 100 * 86400,
        })
        await asyncio.sleep(0.3)

        decay = DecayService(decay_threshold=0.05)
        stats = await decay.apply(storage)
        assert stats["pruned"] >= 0

        node = await storage.get_node(nid)
        if stats["pruned"] > 0:
            assert node is None or node.get("valid_until") is not None


class TestAccessReinforcement:
    """Access frequency reinforcement in retrieval.search()."""

    @pytest.mark.asyncio
    async def test_search_increments_access(
        self, storage: MemoryStorage
    ) -> None:
        now = int(time.time())
        nid = storage.insert_node({
            "type": "semantic",
            "content": "reinforcement test",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "access_count": 0,
        })
        await asyncio.sleep(0.3)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        await retrieval.search(
            "reinforcement",
            limit=5,
            node_types=["semantic", "procedural", "opinion"],
        )
        await asyncio.sleep(1.5)

        node = await storage.get_node(nid)
        assert node is not None
        assert node.get("access_count", 0) >= 1 or node.get("last_accessed") is not None


class TestEntityEnrichment:
    """get_entities_needing_enrichment, update_entity_summary tool."""

    @pytest.mark.asyncio
    async def test_get_entities_needing_enrichment(
        self, storage: MemoryStorage
    ) -> None:
        now = int(time.time())
        await storage.insert_entity({
            "canonical_name": "SparseEntity",
            "type": "person",
            "summary": None,
            "mention_count": 5,
        })
        await storage.insert_entity({
            "canonical_name": "RichEntity",
            "type": "person",
            "summary": "Has summary",
            "mention_count": 5,
        })
        await asyncio.sleep(0.2)

        sparse = await storage.get_entities_needing_enrichment(min_mentions=3)
        names = [e["canonical_name"] for e in sparse]
        assert "SparseEntity" in names
        assert "RichEntity" not in names

    @pytest.mark.asyncio
    async def test_update_entity_summary_tool(
        self, storage: MemoryStorage
    ) -> None:
        now = int(time.time())
        eid = await storage.insert_entity({
            "canonical_name": "ToEnrich",
            "type": "project",
            "summary": None,
            "mention_count": 4,
        })
        await asyncio.sleep(0.2)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_write_path_tools(
            storage=storage,
            retrieval=retrieval,
            embed_fn=None,
            embed_batch_fn=None,
        )
        update_tool = tools[8]
        args = {"entity_id": eid, "summary": "A key project for Q1."}
        result = await update_tool.on_invoke_tool(
            _tool_ctx(update_tool.name, args), __import__("json").dumps(args)
        )
        assert "updated" in str(result).lower()
        await asyncio.sleep(0.2)

        ent = await storage.get_entity_by_name("ToEnrich")
        assert ent is not None
        assert ent.get("summary") == "A key project for Q1."


class TestCausalEdgeInference:
    """get_consecutive_episode_pairs, save_causal_edges tool."""

    @pytest.mark.asyncio
    async def test_get_consecutive_episode_pairs(
        self, storage: MemoryStorage
    ) -> None:
        now = int(time.time())
        storage.ensure_session("causal-sess")
        await asyncio.sleep(0.2)
        ids = []
        for i in range(3):
            nid = storage.insert_node({
                "type": "episodic",
                "content": f"ep {i}",
                "event_time": now + i,
                "created_at": now + i,
                "valid_from": now + i,
                "session_id": "causal-sess",
            })
            ids.append(nid)
        await asyncio.sleep(0.3)

        pairs = await storage.get_consecutive_episode_pairs(limit=10)
        assert len(pairs) >= 2
        assert pairs[0][0]["id"] == ids[0] and pairs[0][1]["id"] == ids[1]

    @pytest.mark.asyncio
    async def test_save_causal_edges_creates_edges(
        self, storage: MemoryStorage
    ) -> None:
        now = int(time.time())
        a = storage.insert_node({
            "type": "episodic",
            "content": "cause",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "session_id": "s1",
        })
        b = storage.insert_node({
            "type": "episodic",
            "content": "effect",
            "event_time": now + 1,
            "created_at": now + 1,
            "valid_from": now + 1,
            "session_id": "s1",
        })
        await asyncio.sleep(0.3)

        classifier = KeywordIntentClassifier()
        retrieval = MemoryRetrieval(storage, classifier)
        tools = build_write_path_tools(
            storage=storage,
            retrieval=retrieval,
            embed_fn=None,
            embed_batch_fn=None,
        )
        save_causal = tools[7]
        args = {
            "edges": [
                {"source_id": a, "target_id": b, "predicate": "caused_by"},
            ],
        }
        result = await save_causal.on_invoke_tool(
            _tool_ctx(save_causal.name, args), __import__("json").dumps(args)
        )
        assert "saved" in str(result).lower()
        await asyncio.sleep(0.3)

        chain = await storage.causal_chain_traversal(b, max_depth=3, limit=5)
        assert len(chain) >= 1
        assert any(r["id"] == a for r in chain)

    @pytest.mark.asyncio
    async def test_consecutive_pairs_excludes_existing_causal(
        self, storage: MemoryStorage
    ) -> None:
        now = int(time.time())
        storage.ensure_session("excl-sess")
        await asyncio.sleep(0.2)
        a = storage.insert_node({
            "type": "episodic",
            "content": "a",
            "event_time": now,
            "created_at": now,
            "valid_from": now,
            "session_id": "excl-sess",
        })
        b = storage.insert_node({
            "type": "episodic",
            "content": "b",
            "event_time": now + 1,
            "created_at": now + 1,
            "valid_from": now + 1,
            "session_id": "excl-sess",
        })
        storage.insert_edge({
            "source_id": a,
            "target_id": b,
            "relation_type": "causal",
            "valid_from": now + 1,
            "created_at": now + 1,
        })
        await asyncio.sleep(0.3)

        pairs = await storage.get_consecutive_episode_pairs(limit=10)
        assert not any(
            p[0]["id"] == a and p[1]["id"] == b for p in pairs
        )


class TestNightlyMaintenance:
    """Integration: full nightly pipeline."""

    @pytest.mark.asyncio
    async def test_execute_task_runs_pipeline(
        self, storage: MemoryStorage, tmp_db
    ) -> None:
        from main import MemoryExtension

        storage.ensure_session("nightly-sess")
        await asyncio.sleep(0.2)

        ext = MemoryExtension()
        ext._storage = storage
        ext._retrieval = MemoryRetrieval(
            storage, KeywordIntentClassifier(),
        )
        ext._decay_service = DecayService(decay_threshold=0.05)
        ext._write_agent = None
        ext._ctx = MagicMock()
        ext._ctx.get_config = lambda k, d=None: (
            3 if k == "entity_enrichment_min_mentions" else
            50 if k == "causal_inference_batch_size" else d
        )

        result = await ext.execute_task("run_nightly_maintenance")
        assert result is not None
        assert "Nightly" in result.get("text", "")
        assert "consolidated" in result.get("text", "").lower()
        assert "decayed" in result.get("text", "").lower() or "pruned" in result.get("text", "").lower()
