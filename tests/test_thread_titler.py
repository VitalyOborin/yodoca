from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from sandbox.extensions.thread_titler.main import (
    REFINE_REQUESTED_TOPIC,
    TITLE_UPDATED_TOPIC,
    ThreadTitlerExtension,
)


def _thread(**overrides):
    data = {
        "id": "thread_1",
        "title": None,
        "title_source": None,
        "title_status": None,
        "title_updated_at": None,
        "channel_id": "web_channel",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_phase1_sets_provisional_title_and_requests_refine():
    ext = ThreadTitlerExtension()
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: default,
        subscribe=lambda event, handler: None,
        subscribe_event=lambda topic, handler: None,
        model_router=None,
        get_thread=AsyncMock(return_value=_thread()),
        update_thread=AsyncMock(
            return_value=_thread(
                title="Need help with Python traceback parsing in logs...",
                title_source="derived",
                title_status="provisional",
                title_updated_at=123,
            )
        ),
        emit=AsyncMock(),
    )
    await ext.initialize(ctx)

    await ext._on_user_message(
        {
            "thread_id": "thread_1",
            "text": (
                "Need help with Python traceback parsing in logs and "
                "figuring out why the parser fails on multiline stack traces."
            ),
        }
    )

    kwargs = ctx.update_thread.await_args.kwargs
    assert kwargs["title_source"] == "derived"
    assert kwargs["title_status"] == "provisional"
    assert ctx.emit.await_args_list[0].args[0] == TITLE_UPDATED_TOPIC
    assert ctx.emit.await_args_list[1].args[0] == REFINE_REQUESTED_TOPIC


@pytest.mark.asyncio
async def test_phase1_skips_already_titled_thread():
    ext = ThreadTitlerExtension()
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: default,
        subscribe=lambda event, handler: None,
        subscribe_event=lambda topic, handler: None,
        model_router=None,
        get_thread=AsyncMock(
            return_value=_thread(title="Manual", title_source="manual")
        ),
        update_thread=AsyncMock(),
        emit=AsyncMock(),
    )
    await ext.initialize(ctx)

    await ext._on_user_message({"thread_id": "thread_1", "text": "hello"})

    ctx.update_thread.assert_not_awaited()
    ctx.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_phase2_refines_provisional_title():
    ext = ThreadTitlerExtension()
    thread = _thread(
        title="Parser error in logs...",
        title_source="derived",
        title_status="provisional",
    )
    updated_thread = _thread(
        title="Fixing parser errors in logs",
        title_source="ai",
        title_status="finalized",
        title_updated_at=456,
    )
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: default,
        subscribe=lambda event, handler: None,
        subscribe_event=lambda topic, handler: None,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
        get_thread=AsyncMock(side_effect=[thread, thread]),
        update_thread=AsyncMock(return_value=updated_thread),
        emit=AsyncMock(),
    )
    await ext.initialize(ctx)

    with patch(
        "sandbox.extensions.thread_titler.main.Runner.run",
        new=AsyncMock(),
    ) as run:
        run.return_value = SimpleNamespace(
            final_output=' "Fixing parser errors in logs." '
        )
        await ext._on_refine_requested(
            SimpleNamespace(
                payload={
                    "thread_id": "thread_1",
                    "message_text": (
                        "Long message about parser failures in logs and "
                        "multiline stack traces."
                    ),
                }
            )
        )

    kwargs = ctx.update_thread.await_args.kwargs
    assert kwargs["title"] == "Fixing parser errors in logs"
    assert kwargs["title_source"] == "ai"
    assert kwargs["title_status"] == "finalized"
    assert ctx.emit.await_args.args[0] == TITLE_UPDATED_TOPIC


@pytest.mark.asyncio
async def test_phase2_skips_if_thread_became_manual():
    ext = ThreadTitlerExtension()
    ctx = SimpleNamespace(
        get_config=lambda key, default=None: default,
        subscribe=lambda event, handler: None,
        subscribe_event=lambda topic, handler: None,
        model_router=SimpleNamespace(get_model=lambda agent_id: "gpt-5-mini"),
        get_thread=AsyncMock(
            side_effect=[
                _thread(
                    title="Derived",
                    title_source="derived",
                    title_status="provisional",
                ),
                _thread(
                    title="Manual",
                    title_source="manual",
                    title_status="finalized",
                ),
            ]
        ),
        update_thread=AsyncMock(),
        emit=AsyncMock(),
    )
    await ext.initialize(ctx)

    with patch(
        "sandbox.extensions.thread_titler.main.Runner.run",
        new=AsyncMock(),
    ) as run:
        run.return_value = SimpleNamespace(final_output="Manual title should win")
        await ext._on_refine_requested(
            SimpleNamespace(
                payload={
                    "thread_id": "thread_1",
                    "message_text": "Please summarize this title.",
                }
            )
        )

    ctx.update_thread.assert_not_awaited()
