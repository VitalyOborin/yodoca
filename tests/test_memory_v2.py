"""Tests for Memory v2 extension."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add memory extension to path
_memory_ext = Path(__file__).resolve().parent.parent / "sandbox" / "extensions" / "memory"
sys.path.insert(0, str(_memory_ext))

from retrieval import (
    EmbeddingIntentClassifier,
    KeywordIntentClassifier,
    MemoryRetrieval,
    classify_query_complexity,
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

        ctx = retrieval.assemble_context(results, token_budget=500)
        assert "Relevant memory" in ctx
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
