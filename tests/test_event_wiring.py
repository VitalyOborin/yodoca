"""Tests for EventWiringManager: system topics, user.message, manifest notify_user."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.events import EventBus
from core.events.topics import SystemTopics
from core.extensions.contract import ChannelProvider
from core.extensions.loader import ExtensionState
from core.extensions.manifest import ExtensionManifest
from core.extensions.routing.event_wiring import EventWiringManager
from core.extensions.routing.router import MessageRouter


class _RecordingChannel(ChannelProvider):
    def __init__(self) -> None:
        self.send_to_user = AsyncMock()
        self.send_message = AsyncMock()


def _manifest_notify(ext_id: str, topic: str) -> ExtensionManifest:
    return ExtensionManifest.model_validate(
        {
            "id": ext_id,
            "name": ext_id.title(),
            "entrypoint": "main:X",
            "depends_on": [],
            "events": {
                "subscribes": [
                    {"topic": topic, "handler": "notify_user"},
                ]
            },
        }
    )


@pytest.mark.asyncio
async def test_system_user_notify_routes_to_router_channel(tmp_path: Path) -> None:
    router = MessageRouter()
    ch = _RecordingChannel()
    router.register_channel("web", ch)
    manager = EventWiringManager(
        router=router,
        manifests=[],
        state={},
        extensions={},
        agent_registry=None,
    )
    bus = EventBus(db_path=tmp_path / "ej.db", poll_interval=0.1, batch_size=5)
    await bus.recover()
    manager.wire(bus)
    await bus.start()
    await bus.publish(
        SystemTopics.USER_NOTIFY,
        "kernel",
        {"text": "hello user", "channel_id": "web"},
    )
    await asyncio.sleep(0.35)
    await bus.stop()

    ch.send_message.assert_awaited_once_with("hello user")


@pytest.mark.asyncio
async def test_system_agent_background_invokes_background_agent(
    tmp_path: Path,
) -> None:
    router = MessageRouter()
    router.invoke_agent_background = AsyncMock(return_value="")  # type: ignore[method-assign]
    manager = EventWiringManager(
        router=router,
        manifests=[],
        state={},
        extensions={},
        agent_registry=None,
    )
    bus = EventBus(db_path=tmp_path / "ej.db", poll_interval=0.1, batch_size=5)
    await bus.recover()
    manager.wire(bus)
    await bus.start()
    await bus.publish(
        SystemTopics.AGENT_BACKGROUND,
        "kernel",
        {"prompt": "silent job", "correlation_id": "c-bg-1"},
    )
    await asyncio.sleep(0.35)
    await bus.stop()

    router.invoke_agent_background.assert_awaited_once_with("silent job")


@pytest.mark.asyncio
async def test_user_message_dispatches_handle_user_message(tmp_path: Path) -> None:
    router = MessageRouter()
    router.configure_thread(
        thread_db_path=str(tmp_path / "thread.db"),
        thread_timeout=1800,
    )
    ch = _RecordingChannel()
    router.register_channel("cli", ch)
    router.handle_user_message = AsyncMock()  # type: ignore[method-assign]

    manager = EventWiringManager(
        router=router,
        manifests=[],
        state={},
        extensions={},
        agent_registry=None,
    )
    bus = EventBus(db_path=tmp_path / "ej.db", poll_interval=0.1, batch_size=5)
    await bus.recover()
    manager.wire(bus)
    await bus.start()

    event_id = await bus.publish(
        "user.message",
        "channel",
        {
            "text": " hi ",
            "user_id": "u42",
            "channel_id": "cli",
            "thread_id": "tid-9",
        },
    )
    await asyncio.sleep(0.35)
    await bus.stop()

    router.handle_user_message.assert_awaited_once()
    args, kwargs = router.handle_user_message.await_args
    assert args[0] == "hi"
    assert args[1] == "u42"
    assert args[2] is ch
    assert args[3] == "cli"
    assert kwargs.get("event_id") == event_id
    assert kwargs.get("thread_id") == "tid-9"


@pytest.mark.asyncio
async def test_user_message_missing_channel_skips_router(tmp_path: Path) -> None:
    router = MessageRouter()
    router.configure_thread(
        thread_db_path=str(tmp_path / "thread.db"),
        thread_timeout=1800,
    )
    router.handle_user_message = AsyncMock()  # type: ignore[method-assign]

    manager = EventWiringManager(
        router=router,
        manifests=[],
        state={},
        extensions={},
        agent_registry=None,
    )
    bus = EventBus(db_path=tmp_path / "ej.db", poll_interval=0.1, batch_size=5)
    await bus.recover()
    manager.wire(bus)
    await bus.start()
    await bus.publish(
        "user.message",
        "channel",
        {"text": "x", "user_id": "u1"},
    )
    await asyncio.sleep(0.35)
    await bus.stop()

    router.handle_user_message.assert_not_called()


@pytest.mark.asyncio
async def test_manifest_notify_user_handler(tmp_path: Path) -> None:
    router = MessageRouter()
    ch = _RecordingChannel()
    router.register_channel("tg", ch)
    manifest = _manifest_notify("alerts_ext", "door.opened")
    manager = EventWiringManager(
        router=router,
        manifests=[manifest],
        state={"alerts_ext": ExtensionState.INACTIVE},
        extensions={"alerts_ext": object()},
        agent_registry=None,
    )
    bus = EventBus(db_path=tmp_path / "ej.db", poll_interval=0.1, batch_size=5)
    await bus.recover()
    manager.wire(bus)
    await bus.start()
    await bus.publish(
        "door.opened",
        "sensor",
        {"text": "Door opened", "channel_id": "tg"},
    )
    await asyncio.sleep(0.35)
    await bus.stop()

    ch.send_message.assert_awaited_once_with("Door opened")
