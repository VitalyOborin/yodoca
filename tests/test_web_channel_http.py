"""Integration tests for web_channel HTTP endpoints."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from sandbox.extensions.web_channel.main import WebChannelExtension


@pytest.fixture
def mock_context():
    """Mock ExtensionContext with thread/project CRUD helpers."""
    ctx = MagicMock()
    scheduler_store = MagicMock()
    scheduler_store.list_all = AsyncMock(return_value=[])
    scheduler_store.insert_one_shot = AsyncMock(return_value=101)
    scheduler_store.insert_recurring = AsyncMock(return_value=202)
    scheduler_store.cancel_one_shot = AsyncMock(return_value=True)
    scheduler_store.cancel_recurring = AsyncMock(return_value=None)
    scheduler_store.update_recurring = AsyncMock(return_value=time.time() + 300)
    scheduler_ext = MagicMock()
    scheduler_ext._store = scheduler_store

    ctx.get_config = MagicMock(
        side_effect=lambda k, d=None: {
            "host": "127.0.0.1",
            "port": 8080,
            "api_key": "",
            "cors_origins": ["*"],
            "request_timeout_seconds": 120,
            "model_name": "yodoca",
            "default_user_id": "web_user",
        }.get(k, d)
    )
    ctx.get_secret = AsyncMock(return_value=None)
    ctx.get_extension = MagicMock(
        side_effect=lambda extension_id: (
            scheduler_ext if extension_id == "scheduler" else None
        )
    )
    ctx.emit = AsyncMock()
    ctx.list_threads = AsyncMock(return_value=[])
    ctx.create_thread = AsyncMock(
        return_value={
            "id": "sess_1",
            "project_id": None,
            "title": None,
            "channel_id": "web_channel",
            "created_at": 1773096500,
            "last_active_at": 1773096583,
            "is_archived": False,
        }
    )
    ctx.get_thread = AsyncMock(return_value=None)
    ctx.update_thread = AsyncMock(return_value=None)
    ctx.archive_thread = AsyncMock(return_value=False)
    ctx.get_thread_history = AsyncMock(return_value=None)
    ctx.list_projects = AsyncMock(return_value=[])
    ctx.get_project = AsyncMock(return_value=None)
    ctx.create_project = AsyncMock(
        return_value={
            "id": "proj_1",
            "name": "Alpha",
            "description": "Alpha description",
            "icon": "🚀",
            "instructions": "Use strict mode.",
            "agent_config": {"model": "gpt-5"},
            "created_at": 1773096500,
            "updated_at": 1773096583,
            "files": ["README.md"],
            "links": ["https://example.com/spec"],
        }
    )
    ctx.update_project = AsyncMock(
        return_value={
            "id": "proj_1",
            "name": "Alpha 2",
            "description": "Updated description",
            "icon": "🧠",
            "instructions": "Use safe mode.",
            "agent_config": {"model": "gpt-5-mini"},
            "created_at": 1773096500,
            "updated_at": 1773096600,
            "files": ["docs/guide.md"],
            "links": ["https://example.com/guide"],
        }
    )
    ctx.delete_project = AsyncMock(return_value=False)
    ctx._scheduler_store = scheduler_store
    ctx._scheduler_ext = scheduler_ext
    return ctx


@pytest.fixture
def web_channel_app(mock_context):
    """Create web channel extension and app with mocked context."""
    ext = WebChannelExtension()
    asyncio.run(ext.initialize(mock_context))
    from sandbox.extensions.web_channel.app import create_app

    return create_app(ext)


def test_get_models(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) >= 1
    assert data["data"][0]["id"] == "yodoca"


def test_get_health(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data


def test_get_threads(web_channel_app, mock_context):
    mock_context.list_threads.return_value = [
        {
            "id": "sess_1",
            "project_id": None,
            "title": "First",
            "channel_id": "web_channel",
            "created_at": 1773096500,
            "last_active_at": 1773096583,
            "is_archived": False,
        },
        {
            "id": "sess_2",
            "project_id": "proj_1",
            "title": None,
            "channel_id": "cli_channel",
            "created_at": 1773096501,
            "last_active_at": 1773096584,
            "is_archived": False,
        },
    ]
    client = TestClient(web_channel_app)
    resp = client.get("/api/threads")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["threads"]) == 2
    assert data["threads"][0]["last_active_at"] == 1773096583


def test_post_threads(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.post("/api/threads", json={"title": "New thread"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread"]["id"] == "sess_1"
    assert data["thread"]["channel_id"] == "web_channel"


def test_get_thread_detail_ok(web_channel_app, mock_context):
    mock_context.get_thread.return_value = {
        "id": "sess_1",
        "project_id": None,
        "title": "Saved",
        "channel_id": "web_channel",
        "created_at": 1773096500,
        "last_active_at": 1773096583,
        "is_archived": False,
    }
    mock_context.get_thread_history.return_value = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]
    client = TestClient(web_channel_app)
    resp = client.get("/api/threads/sess_1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread"]["id"] == "sess_1"
    assert len(data["history"]) == 2


def test_get_thread_detail_not_found(web_channel_app, mock_context):
    mock_context.get_thread.return_value = None
    mock_context.get_thread_history.return_value = None
    client = TestClient(web_channel_app)
    resp = client.get("/api/threads/missing_id")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "thread_not_found"


def test_patch_thread_ok(web_channel_app, mock_context):
    mock_context.update_thread.return_value = {
        "id": "sess_1",
        "project_id": "proj_1",
        "title": "Renamed",
        "channel_id": "web_channel",
        "created_at": 1773096500,
        "last_active_at": 1773096583,
        "is_archived": False,
    }
    client = TestClient(web_channel_app)
    resp = client.patch(
        "/api/threads/sess_1",
        json={
            "title": "Renamed",
            "project_id": "proj_1",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["thread"]["project_id"] == "proj_1"


def test_delete_thread_archives(web_channel_app, mock_context):
    mock_context.archive_thread.return_value = True
    client = TestClient(web_channel_app)
    resp = client.delete("/api/threads/sess_1")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_get_projects(web_channel_app, mock_context):
    mock_context.list_projects.return_value = [
        {
            "id": "proj_1",
            "name": "Alpha",
            "description": "Alpha description",
            "icon": "🚀",
            "instructions": "Use strict mode.",
            "agent_config": {"model": "gpt-5"},
            "created_at": 1773096500,
            "updated_at": 1773096583,
            "files": ["README.md"],
            "links": ["https://example.com/spec"],
        }
    ]
    client = TestClient(web_channel_app)
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert data["projects"][0]["name"] == "Alpha"
    assert data["projects"][0]["description"] == "Alpha description"
    assert data["projects"][0]["icon"] == "🚀"
    assert data["projects"][0]["links"] == ["https://example.com/spec"]


def test_get_project_ok(web_channel_app, mock_context):
    mock_context.get_project.return_value = {
        "id": "proj_1",
        "name": "Alpha",
        "description": "Alpha description",
        "icon": "🚀",
        "instructions": "Use strict mode.",
        "agent_config": {"model": "gpt-5"},
        "created_at": 1773096500,
        "updated_at": 1773096583,
        "files": ["README.md"],
        "links": ["https://example.com/spec"],
    }
    client = TestClient(web_channel_app)
    resp = client.get("/api/projects/proj_1")
    assert resp.status_code == 200
    assert resp.json()["project"]["id"] == "proj_1"
    assert resp.json()["project"]["icon"] == "🚀"


def test_post_projects(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.post(
        "/api/projects",
        json={
            "name": "Alpha",
            "description": "Alpha description",
            "icon": "🚀",
            "instructions": "Use strict mode.",
            "agent_config": {"model": "gpt-5"},
            "files": ["README.md"],
            "links": ["https://example.com/spec"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["project"]["id"] == "proj_1"
    assert resp.json()["project"]["links"] == ["https://example.com/spec"]


def test_patch_projects(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.patch(
        "/api/projects/proj_1",
        json={
            "name": "Alpha 2",
            "description": "Updated description",
            "icon": "🧠",
            "instructions": "Use safe mode.",
            "agent_config": {"model": "gpt-5-mini"},
            "files": ["docs/guide.md"],
            "links": ["https://example.com/guide"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["project"]["name"] == "Alpha 2"
    assert resp.json()["project"]["icon"] == "🧠"


def test_patch_projects_partial_does_not_clear_links(web_channel_app, mock_context):
    client = TestClient(web_channel_app)
    resp = client.patch(
        "/api/projects/proj_1",
        json={
            "description": "Only description changed",
            "icon": "📌",
        },
    )
    assert resp.status_code == 200
    kwargs = mock_context.update_project.await_args.kwargs
    assert kwargs["description"] == "Only description changed"
    assert kwargs["icon"] == "📌"
    assert "links" in kwargs


def test_delete_projects(web_channel_app, mock_context):
    mock_context.delete_project.return_value = True
    client = TestClient(web_channel_app)
    resp = client.delete("/api/projects/proj_1")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_get_notifications_empty(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.get("/api/notifications?timeout=1")
    assert resp.status_code == 200
    assert resp.json()["notifications"] == []


def test_post_chat_completions_no_user_message(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "yodoca", "messages": [{"role": "assistant", "content": "hi"}]},
    )
    assert resp.status_code == 422


def test_post_chat_completions_busy_503(web_channel_app):
    ext = web_channel_app.state.extension
    bridge = ext._bridge

    async def hold_busy():
        await bridge.acquire()

    asyncio.run(hold_busy())

    client = TestClient(web_channel_app)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "yodoca",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    bridge.release()
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers
    assert resp.json()["error"]["code"] == "busy"


def test_post_responses_non_stream_when_router_uses_stream_callbacks(
    web_channel_app, mock_context
):
    ext = web_channel_app.state.extension

    async def emit_with_stream_callbacks(topic, payload):
        assert topic == "user.message"
        await ext.on_stream_start(payload["user_id"])
        await ext.on_stream_chunk(payload["user_id"], "Hello")
        await ext.on_stream_end(payload["user_id"], "Hello")

    mock_context.emit.side_effect = emit_with_stream_callbacks

    client = TestClient(web_channel_app)
    resp = client.post(
        "/v1/responses",
        json={
            "model": "yodoca",
            "messages": [
                {"role": "user", "content": "Say hello in one short sentence."}
            ],
            "stream": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["output"][0]["content"][0]["text"] == "Hello"


def test_get_schedules_status_active_alias(web_channel_app, mock_context):
    now = time.time()
    mock_context._scheduler_store.list_all.return_value = [
        {
            "id": 1,
            "type": "one_shot",
            "topic": "system.user.notify",
            "payload": '{"text":"One shot"}',
            "fire_at_or_next": now + 120,
            "status": "scheduled",
            "created_at": now,
        },
        {
            "id": 2,
            "type": "one_shot",
            "topic": "system.user.notify",
            "payload": '{"text":"Done"}',
            "fire_at_or_next": now - 120,
            "status": "fired",
            "created_at": now - 10,
        },
        {
            "id": 3,
            "type": "recurring",
            "topic": "system.agent.background",
            "payload": '{"prompt":"Ping"}',
            "fire_at_or_next": now + 300,
            "status": "active",
            "cron_expr": "*/5 * * * *",
            "every_sec": None,
            "until_at": None,
            "created_at": now - 20,
        },
    ]
    client = TestClient(web_channel_app)
    resp = client.get("/api/schedules?status=active")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert all(item["status"] in {"scheduled", "active"} for item in data["schedules"])
    assert any(item["type"] == "one_shot" for item in data["schedules"])
    assert any(item["type"] == "recurring" for item in data["schedules"])
    assert all(item["fires_at_iso"].endswith("Z") for item in data["schedules"])


def test_post_schedules_once_created(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.post(
        "/api/schedules/once",
        json={
            "topic": "system.user.notify",
            "message": "Reminder",
            "delay_seconds": 60,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True
    assert data["schedule_id"] == 101
    assert data["status"] == "scheduled"


def test_post_schedules_once_invalid_xor(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.post(
        "/api/schedules/once",
        json={
            "topic": "system.user.notify",
            "message": "Reminder",
            "delay_seconds": 60,
            "at_iso": "2026-03-16T09:00:00",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_schedule_payload"


def test_post_schedules_recurring_created(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.post(
        "/api/schedules/recurring",
        json={
            "topic": "system.agent.background",
            "message": "Run check",
            "every_seconds": 120,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True
    assert data["schedule_id"] == 202
    assert data["status"] == "created"
    assert data["next_fire_iso"].endswith("Z")


def test_post_schedules_recurring_invalid_cron(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.post(
        "/api/schedules/recurring",
        json={
            "topic": "system.agent.background",
            "message": "Run check",
            "cron": "not a cron",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_cron"


def test_delete_schedule_one_shot_conflict(web_channel_app, mock_context):
    now = time.time()
    mock_context._scheduler_store.list_all.return_value = [
        {
            "id": 11,
            "type": "one_shot",
            "topic": "system.user.notify",
            "payload": '{"text":"Done"}',
            "fire_at_or_next": now - 10,
            "status": "fired",
            "created_at": now - 100,
        }
    ]
    client = TestClient(web_channel_app)
    resp = client.delete("/api/schedules/one_shot/11")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "schedule_conflict"


def test_patch_schedule_one_shot_422(web_channel_app):
    client = TestClient(web_channel_app)
    resp = client.patch(
        "/api/schedules/one_shot/11",
        json={"status": "paused"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "one_shot_update_not_supported"


def test_patch_schedule_until_null_sets_set_until(web_channel_app, mock_context):
    client = TestClient(web_channel_app)
    resp = client.patch(
        "/api/schedules/recurring/77",
        json={"until_iso": None},
    )
    assert resp.status_code == 200
    assert resp.json()["next_fire_iso"].endswith("Z")
    kwargs = mock_context._scheduler_store.update_recurring.await_args.kwargs
    assert kwargs["set_until"] is True
    assert kwargs["until_at"] is None


def test_scheduler_endpoints_return_503_when_extension_missing(mock_context):
    mock_context.get_extension = MagicMock(return_value=None)
    ext = WebChannelExtension()
    asyncio.run(ext.initialize(mock_context))
    from sandbox.extensions.web_channel.app import create_app

    client = TestClient(create_app(ext))
    resp = client.get("/api/schedules")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "scheduler_unavailable"

