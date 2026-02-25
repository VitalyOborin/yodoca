"""Integration test: proactive_dispatcher end-to-end.

Scenario A: task.received -> task_agent.invoke() -> notify_user via MockChannel.
Asserts: task_agent.invoke was called, event journal has task.received done, user got response.
"""

import asyncio
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.events import EventBus
from core.extensions import Loader, MessageRouter
from core.llm import ModelRouter
from core.settings import load_settings


def _setup_test_extensions(tmp_path: Path, project_root: Path) -> Path:
    """Copy task_source, task_agent to tmp; add recording channel. Returns extensions_dir."""
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    for name in ("task_source", "task_agent"):
        src = project_root / "sandbox" / "extensions" / name
        if src.exists():
            shutil.copytree(src, ext_dir / name)
    # Add minimal recording channel extension (ChannelProvider that stores sent messages)
    (ext_dir / "recording_channel").mkdir()
    (ext_dir / "recording_channel" / "manifest.yaml").write_text(
        "id: recording_channel\nname: Recording Channel\nversion: 1.0.0\n"
        "entrypoint: main:RecordingChannelExtension\ndepends_on: []\nenabled: true\n",
        encoding="utf-8",
    )
    (ext_dir / "recording_channel" / "main.py").write_text(
        '"""Recording channel for tests."""\n'
        "from core.extensions.contract import ChannelProvider\n\n"
        "class RecordingChannelExtension:\n"
        "    def __init__(self):\n"
        "        self.sent = []\n"
        "    async def initialize(self, ctx): pass\n"
        "    async def start(self): pass\n"
        "    async def stop(self): pass\n"
        "    async def destroy(self): pass\n"
        "    def health_check(self): return True\n"
        "    async def send_to_user(self, user_id, message):\n"
        "        self.sent.append((user_id, message))\n"
        "    async def send_message(self, message):\n"
        "        self.sent.append(('default', message))\n",
        encoding="utf-8",
    )
    return ext_dir


@pytest.mark.asyncio
async def test_proactive_dispatcher_end_to_end(tmp_path: Path) -> None:
    """task.received -> task_agent.invoke -> notify_user; event journal done; user got response."""
    project_root = Path(__file__).resolve().parent.parent
    task_source = project_root / "sandbox" / "extensions" / "task_source"
    task_agent = project_root / "sandbox" / "extensions" / "task_agent"
    if not task_source.exists() or not task_agent.exists():
        pytest.skip(
            "task_source and task_agent extensions not found in sandbox/extensions; "
            "e2e requires these to be present"
        )
    extensions_dir = _setup_test_extensions(tmp_path, project_root)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    journal_path = data_dir / "event_journal.db"

    settings = load_settings()
    model_router = ModelRouter(settings=settings, secrets_getter=lambda _: None)
    shutdown_event = asyncio.Event()
    router = MessageRouter()

    loader = Loader(extensions_dir=extensions_dir, data_dir=data_dir)
    loader.set_shutdown_event(shutdown_event)
    loader.set_model_router(model_router)

    event_bus = EventBus(db_path=journal_path, poll_interval=0.2)
    await event_bus.recover()
    loader.set_event_bus(event_bus)

    await loader.discover()
    await loader.load_all()
    await loader.initialize_all(router)
    loader.detect_and_wire_all(router)
    loader.wire_event_subscriptions(event_bus)

    recording_channel_ext = router.get_channel("recording_channel")
    assert recording_channel_ext is not None, "recording_channel should be registered"

    # Mock Runner.run so task_agent (DeclarativeAgentAdapter) returns without calling LLM
    mock_result = MagicMock()
    mock_result.final_output = "Task completed: daily report summarized"

    with patch("core.extensions.declarative_agent.Runner") as mock_runner:
        mock_runner.run = AsyncMock(return_value=mock_result)

        await event_bus.start()
        await loader.start_all()

        # Wait for task_source to emit and proactive handler to process (poll_interval 0.2)
        for _ in range(25):
            await asyncio.sleep(0.2)
            if recording_channel_ext.sent:
                break

        shutdown_event.set()
        await asyncio.sleep(0.5)
        await event_bus.stop()
        await loader.shutdown()

    # Assert 1: task_agent.invoke was called (Runner.run called with task containing payload)
    assert mock_runner.run.called, "Runner.run (via task_agent.invoke) should have been called"
    call_args = mock_runner.run.call_args
    assert call_args is not None
    task_passed = call_args[0][1] if len(call_args[0]) > 1 else str(call_args)
    assert "task.received" in task_passed
    assert "Summarize daily report" in task_passed

    # Assert 2: event journal has task.received with status done
    conn = sqlite3.connect(journal_path)
    cur = conn.execute(
        "SELECT id, topic, status FROM event_journal WHERE topic = 'task.received'"
    )
    rows = cur.fetchall()
    conn.close()
    assert len(rows) >= 1, "event_journal should have task.received"
    topics_statuses = [(r[1], r[2]) for r in rows]
    assert any(s == "done" for _, s in topics_statuses), (
        f"task.received should have status done, got: {topics_statuses}"
    )

    # Assert 3: user received response via channel
    assert len(recording_channel_ext.sent) >= 1, "User should have received message via channel"
    assert any(
        "Task completed" in msg for _, msg in recording_channel_ext.sent
    ), f"Expected 'Task completed' in channel output, got: {recording_channel_ext.sent}"
