"""Tests for session/project persistence and project context injection."""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.extensions.contract import ExtensionState, TurnContext
from core.extensions.loader import Loader
from core.extensions.project_repository import ProjectRepository
from core.extensions.project_service import ProjectService
from core.extensions.router import MessageRouter
from core.extensions.session_repository import SessionRepository


def _seed_legacy_session_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE agent_sessions (
            session_id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            message_data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE yodoca_session_meta (
            session_id TEXT PRIMARY KEY,
            updated_at INTEGER NOT NULL
        );
        INSERT INTO agent_sessions (session_id, created_at, updated_at)
        VALUES ('sess_legacy', '2026-03-09 10:00:00', '2026-03-09 10:10:00');
        INSERT INTO agent_messages (session_id, message_data, created_at)
        VALUES (
            'sess_legacy',
            '{"role":"user","content":"hello"}',
            '2026-03-09 10:20:00'
        );
        INSERT INTO yodoca_session_meta (session_id, updated_at)
        VALUES ('sess_legacy', 1773000000);
        """
    )
    conn.commit()
    conn.close()


def test_session_repository_migrates_legacy_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "session.db"
    _seed_legacy_session_db(db_path)

    repo = SessionRepository(str(db_path))

    session = repo.get_session("sess_legacy", include_archived=True)
    assert session is not None
    assert session["id"] == "sess_legacy"
    assert session["channel_id"] == "unknown"
    assert session["created_at"] == 1773050400
    assert session["last_active_at"] == 1773051600
    assert repo.get_session_history("sess_legacy") == [
        {"role": "user", "content": "hello"}
    ]


def test_project_service_binds_and_unbinds_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "session.db"
    sessions = SessionRepository(str(db_path))
    projects = ProjectRepository(str(db_path))
    service = ProjectService(projects, sessions)

    sessions.create_session(
        session_id="sess_1",
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
    assert project["id"] == "proj_1"

    updated = service.bind_session("sess_1", "proj_1")
    assert updated is not None
    assert updated["project_id"] == "proj_1"

    project_updated = service.update_project(
        "proj_1",
        name="Alpha 2",
        instructions="Use safe mode.",
        agent_config={"model": "gpt-5-mini"},
        files=["docs/guide.md"],
        now_ts=1773096600,
    )
    assert project_updated is not None
    assert project_updated["files"] == ["docs/guide.md"]
    unchanged = service.update_project("proj_1", now_ts=1773096700)
    assert unchanged is not None
    assert unchanged["updated_at"] == 1773096600

    assert service.list_projects()[0]["name"] == "Alpha 2"
    assert service.get_project("proj_1")["name"] == "Alpha 2"
    assert service.delete_project("proj_1") is True
    assert sessions.get_session("sess_1", include_archived=True)["project_id"] is None


@pytest.mark.asyncio
async def test_router_persists_explicit_web_session(tmp_path: Path) -> None:
    router = MessageRouter()
    router.configure_session(
        session_db_path=str(tmp_path / "session.db"),
        session_timeout=1800,
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
            session_id="sess_web",
        )

    session = await router.get_session("sess_web", include_archived=True)
    assert session is not None
    assert session["channel_id"] == "web_channel"
    assert session["id"] == "sess_web"


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
    router.configure_session(
        session_db_path=str(tmp_path / "session.db"),
        session_timeout=1800,
    )
    project = await router.create_project(
        name="Alpha",
        instructions="Project rule.",
        agent_config={"model": "gpt-5"},
        files=[],
        now_ts=1773096500,
    )
    await router.create_session(
        session_id="sess_ctx",
        channel_id="cli_channel",
        project_id=project["id"],
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
            session_id="sess_ctx",
        ),
    )

    assert enriched.index("[Project Instructions]") < enriched.index("[Memory]")
