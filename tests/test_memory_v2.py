"""Tests for Memory v2 extension."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add memory extension to path
_memory_ext = Path(__file__).resolve().parent.parent / "sandbox" / "extensions" / "memory"
sys.path.insert(0, str(_memory_ext))

from retrieval import KeywordIntentClassifier, MemoryRetrieval, classify_query_complexity
from storage import MemoryStorage


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
        results = await retrieval.search("budget", limit=5)
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
