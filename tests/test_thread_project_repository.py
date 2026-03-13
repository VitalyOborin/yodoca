"""Tests for thread/project persistence and project context injection."""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.extensions.contract import ExtensionState, TurnContext
from core.extensions.loader import Loader
from core.extensions.persistence.project_repository import ProjectRepository
from core.extensions.persistence.project_service import ProjectService
from core.extensions.persistence.thread_repository import ThreadRepository
from core.extensions.routing.router import MessageRouter


def _seed_agent_messages(db_path: Path, thread_id: str, message_data: str) -> None:
    """Create agent_messages table and insert a message (SDK-owned table)."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            message_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        "INSERT INTO agent_messages (session_id, message_data, created_at) "
        "VALUES (?, ?, ?)",
        (thread_id, message_data, "2026-03-09 10:20:00"),
    )
    conn.commit()
    conn.close()


def test_thread_repository_creates_and_retrieves_thread(tmp_path: Path) -> None:
    db_path = tmp_path / "thread.db"
    repo = ThreadRepository(str(db_path))

    thread = repo.create_thread(
        thread_id="sess_1",
        channel_id="web_channel",
        project_id=None,
        title="Draft",
        now_ts=1773096500,
    )
    assert thread is not None
    assert thread.id == "sess_1"
    assert thread.channel_id == "web_channel"
    assert thread.created_at == 1773096500
    assert thread.last_active_at == 1773096500

    retrieved = repo.get_thread("sess_1", include_archived=True)
    assert retrieved is not None
    assert retrieved.id == "sess_1"
    assert retrieved.channel_id == "web_channel"


def test_thread_repository_get_thread_history(tmp_path: Path) -> None:
    db_path = tmp_path / "thread.db"
    repo = ThreadRepository(str(db_path))
    repo.create_thread(
        thread_id="sess_1",
        channel_id="web_channel",
        project_id=None,
        title=None,
        now_ts=1773096500,
    )
    _seed_agent_messages(db_path, "sess_1", '{"role":"user","content":"hello"}')

    history = repo.get_thread_history("sess_1")
    assert history is not None
    assert len(history) == 1
    first = history[0]
    assert first["role"] == "user"
    assert first["content"] == "hello"
    assert isinstance(first.get("created_at"), int)


@pytest.mark.asyncio
async def test_unicode_stored_without_escaping_in_message_data(tmp_path: Path) -> None:
    """Unicode in message_data is stored as-is, not escaped (ensure_ascii=False)."""
    from core.extensions.persistence.thread_manager import ThreadManager

    manager = ThreadManager()
    db_path = tmp_path / "thread.db"
    manager.configure_thread(
        thread_db_path=str(db_path),
        thread_timeout=1800,
        event_bus=None,
        now_ts=1000.0,
    )
    thread = manager.get_or_create_thread("sess_unicode", "cli")
    cyrillic_content = "Привет, мир!"
    await thread.add_items([{"role": "user", "content": cyrillic_content}])

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT message_data FROM agent_messages WHERE session_id = ?",
            ("sess_unicode",),
        ).fetchone()
    assert row is not None
    assert cyrillic_content in row[0]
    assert "\\u041f" not in row[0]


def test_project_service_binds_and_unbinds_threads(tmp_path: Path) -> None:
    db_path = tmp_path / "thread.db"
    threads = ThreadRepository(str(db_path))
    projects = ProjectRepository(str(db_path))
    service = ProjectService(projects, threads)

    threads.create_thread(
        thread_id="sess_1",
        channel_id="web_channel",
        project_id=None,
        title="Draft",
        now_ts=1773096500,
    )
    project = service.create_project(
        name="Alpha",
        instructions="Use strict mode.",
        agent_config={"model": "gpt-5"},
        files=["README.md"],
        now_ts=1773096500,
        project_id="proj_1",
    )
    assert project.id == "proj_1"

    updated = service.bind_thread("sess_1", "proj_1")
    assert updated is not None
    assert updated.project_id == "proj_1"

    project_updated = service.update_project(
        "proj_1",
        name="Alpha 2",
        instructions="Use safe mode.",
        agent_config={"model": "gpt-5-mini"},
        files=["docs/guide.md"],
        now_ts=1773096600,
    )
    assert project_updated is not None
    assert project_updated.files == ["docs/guide.md"]
    unchanged = service.update_project("proj_1", now_ts=1773096700)
    assert unchanged is not None
    assert unchanged.updated_at == 1773096600

    assert service.list_projects()[0].name == "Alpha 2"
    assert service.get_project("proj_1").name == "Alpha 2"
    assert service.delete_project("proj_1") is True
    assert threads.get_thread("sess_1", include_archived=True).project_id is None


@pytest.mark.asyncio
async def test_router_persists_explicit_web_thread(tmp_path: Path) -> None:
    router = MessageRouter()
    router.configure_thread(
        thread_db_path=str(tmp_path / "thread.db"),
        thread_timeout=1800,
    )
    channel = MagicMock()
    channel.send_to_user = AsyncMock()
    router.set_agent(MagicMock())

    with patch("agents.Runner") as mock_runner:
        result = MagicMock()
        result.final_output = "ok"
        mock_runner.run = AsyncMock(return_value=result)
        await router.handle_user_message(
            "hello",
            "web_user",
            channel,
            "web_channel",
            thread_id="sess_web",
        )

    thread = await router.thread_manager.get_thread(
        "sess_web",
        include_archived=True,
    )
    assert thread is not None
    assert thread.channel_id == "web_channel"
    assert thread.id == "sess_web"


@pytest.mark.asyncio
async def test_loader_injects_project_context_before_memory(tmp_path: Path) -> None:
    class DummyMemoryProvider:
        @property
        def context_priority(self) -> int:
            return 50

        async def get_context(
            self, prompt: str, turn_context: TurnContext
        ) -> str | None:
            return "[Memory]\nremembered"

    router = MessageRouter()
    router.configure_thread(
        thread_db_path=str(tmp_path / "thread.db"),
        thread_timeout=1800,
    )
    project_service = router.project_service
    assert project_service is not None
    project = project_service.create_project(
        name="Alpha",
        instructions="Project rule.",
        agent_config={"model": "gpt-5"},
        files=[],
        now_ts=1773096500,
    )
    router.thread_manager.thread_repository.create_thread(
        thread_id="sess_ctx",
        channel_id="cli_channel",
        project_id=project.id,
        title=None,
        now_ts=1773096501,
    )

    loader = Loader(
        extensions_dir=tmp_path, data_dir=tmp_path, settings={"extensions": {}}
    )
    loader._extensions = {"memory": DummyMemoryProvider()}
    loader._state = {"memory": ExtensionState.ACTIVE}
    router.register_channel("cli_channel", MagicMock())
    router.set_channel_descriptions({"cli_channel": "CLI"})
    loader.wire_context_providers(router)

    enriched = await router.enrich_prompt(
        "hello",
        turn_context=TurnContext(
            agent_id="orchestrator",
            channel_id="cli_channel",
            user_id="user1",
            thread_id="sess_ctx",
        ),
    )

    assert enriched.index("[Project Instructions]") < enriched.index("[Memory]")



