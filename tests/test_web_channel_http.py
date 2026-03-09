"""Integration tests for web_channel HTTP endpoints."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from sandbox.extensions.web_channel.main import WebChannelExtension


@pytest.fixture
def mock_context():
    """Mock ExtensionContext with emit and list_sessions."""
    ctx = MagicMock()
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
    ctx.emit = AsyncMock()
    ctx.list_sessions = MagicMock(return_value=[])
    ctx.delete_session = MagicMock(return_value=False)
    return ctx


@pytest.fixture
def web_channel_app(mock_context):
    """Create web channel extension and app with mocked context."""
    ext = WebChannelExtension()
    asyncio.run(ext.initialize(mock_context))
    from sandbox.extensions.web_channel.app import create_app

    return create_app(ext)


def test_get_models(web_channel_app):
    """GET /v1/models returns virtual model list."""
    client = TestClient(web_channel_app)
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) >= 1
    assert data["data"][0]["id"] == "yodoca"


def test_get_health(web_channel_app):
    """GET /api/health returns status and uptime."""
    client = TestClient(web_channel_app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data


def test_get_conversations(web_channel_app, mock_context):
    """GET /api/conversations returns session list."""
    mock_context.list_sessions.return_value = ["sess_1", "sess_2"]
    client = TestClient(web_channel_app)
    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert "conversations" in data
    assert len(data["conversations"]) == 2


def test_delete_conversation_not_found(web_channel_app, mock_context):
    """DELETE /api/conversations/{id} returns 404 when session missing."""
    mock_context.delete_session.return_value = False
    client = TestClient(web_channel_app)
    resp = client.delete("/api/conversations/missing_id")
    assert resp.status_code == 404


def test_delete_conversation_ok(web_channel_app, mock_context):
    """DELETE /api/conversations/{id} returns 200 when deleted."""
    mock_context.delete_session.return_value = True
    client = TestClient(web_channel_app)
    resp = client.delete("/api/conversations/sess_1")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_get_notifications_empty(web_channel_app):
    """GET /api/notifications returns empty list when no notifications."""
    client = TestClient(web_channel_app)
    resp = client.get("/api/notifications?timeout=1")
    assert resp.status_code == 200
    assert resp.json()["notifications"] == []


def test_post_chat_completions_no_user_message(web_channel_app):
    """POST /v1/chat/completions with no user message returns 422."""
    client = TestClient(web_channel_app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "yodoca", "messages": [{"role": "assistant", "content": "hi"}]},
    )
    assert resp.status_code == 422


def test_post_chat_completions_busy_503(web_channel_app):
    """POST /v1/chat/completions when bridge reports busy returns 503."""
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
    """POST /v1/responses returns JSON even if router path is streaming callbacks."""
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
            "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
            "stream": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["output"][0]["content"][0]["text"] == "Hello"
