import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from sandbox.extensions.thread_titler.main import (
    TITLE_UPDATED_TOPIC,
    ThreadTitlerExtension,
)


def _thread(**overrides):
    data = {
        "id": "thread_1",
        "title": None,
        "channel_id": "web_channel",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_phase1_sets_provisional_title_and_starts_parallel_refine():
    ext = ThreadTitlerExtension()
    updated = _thread(title="Need help with Python traceback parsing in logs...")
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: default,
        subscribe=lambda event, handler: None,
        model_router=None,
        get_thread=AsyncMock(return_value=_thread()),
        update_thread=AsyncMock(return_value=updated),
        emit=AsyncMock(),
    )
    await ext.initialize(ctx)

    with patch.object(ext, "_run_refine", new=AsyncMock()) as run_refine:
        await ext._on_user_message(
            {
                "thread_id": "thread_1",
                "text": (
                    "Need help with Python traceback parsing in logs and "
                    "figuring out why the parser fails on multiline stack traces."
                ),
            }
        )
        await asyncio.sleep(0)

    kwargs = ctx.update_thread.await_args.kwargs
    assert kwargs["title"] == "Need help with Python traceback parsing in logs..."
    assert ctx.emit.await_args.args[0] == TITLE_UPDATED_TOPIC
    run_refine.assert_awaited_once()


@pytest.mark.asyncio
async def test_phase1_skips_already_titled_thread():
    ext = ThreadTitlerExtension()
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: default,
        subscribe=lambda event, handler: None,
        model_router=None,
        get_thread=AsyncMock(return_value=_thread(title="Existing title")),
        update_thread=AsyncMock(),
        emit=AsyncMock(),
    )
    await ext.initialize(ctx)

    await ext._on_user_message({"thread_id": "thread_1", "text": "hello"})

    ctx.update_thread.assert_not_awaited()
    ctx.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_phase2_refines_only_if_title_still_matches_provisional():
    ext = ThreadTitlerExtension()
    provisional = "Parser error in logs..."
    updated_thread = _thread(title="Fixing parser errors in logs")
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: default,
        subscribe=lambda event, handler: None,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
        get_thread=AsyncMock(
            side_effect=[
                _thread(title=provisional),
                _thread(title=provisional),
            ]
        ),
        update_thread=AsyncMock(return_value=updated_thread),
        emit=AsyncMock(),
    )
    await ext.initialize(ctx)

    with patch(
        "sandbox.extensions.thread_titler.main.Runner.run",
        new=AsyncMock(
            return_value=SimpleNamespace(
                final_output=' "Fixing parser errors in logs." '
            )
        ),
    ):
        await ext._run_refine(
            thread_id="thread_1",
            message_text="Long message about parser failures in logs.",
            provisional_title=provisional,
        )

    assert (
        ctx.update_thread.await_args.kwargs["title"] == "Fixing parser errors in logs"
    )
    assert ctx.emit.await_args.args[0] == TITLE_UPDATED_TOPIC


@pytest.mark.asyncio
async def test_phase2_skips_if_title_changed_before_refine_finishes():
    ext = ThreadTitlerExtension()
    provisional = "Derived"
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: default,
        subscribe=lambda event, handler: None,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
        get_thread=AsyncMock(
            side_effect=[
                _thread(title=provisional),
                _thread(title="Manual rename"),
            ]
        ),
        update_thread=AsyncMock(),
        emit=AsyncMock(),
    )
    await ext.initialize(ctx)

    with patch(
        "sandbox.extensions.thread_titler.main.Runner.run",
        new=AsyncMock(return_value=SimpleNamespace(final_output="Manual should win")),
    ):
        await ext._run_refine(
            thread_id="thread_1",
            message_text="Please summarize this title.",
            provisional_title=provisional,
        )

    ctx.update_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_cancels_active_refine_tasks():
    ext = ThreadTitlerExtension()
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: default,
        subscribe=lambda event, handler: None,
        model_router=None,
        get_thread=AsyncMock(),
        update_thread=AsyncMock(),
        emit=AsyncMock(),
    )
    await ext.initialize(ctx)

    started = asyncio.Event()

    async def fake_run_refine(**kwargs):
        started.set()
        await asyncio.sleep(10)

    with patch.object(ext, "_run_refine", side_effect=fake_run_refine):
        ext._spawn_refine_task(
            thread_id="thread_1",
            message_text="long message",
            provisional_title="provisional",
        )
        await started.wait()
        await ext.stop()

    assert not ext._active_refine_tasks
