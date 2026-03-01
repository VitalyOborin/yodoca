"""Tests for task_engine worker prompt construction."""

import sys
from pathlib import Path

_TASK_ENGINE_DIR = (
    Path(__file__).resolve().parent.parent / "sandbox" / "extensions" / "task_engine"
)
if str(_TASK_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_TASK_ENGINE_DIR))

from state import TaskState  # type: ignore[import-not-found]
from worker import _build_step_prompt  # type: ignore[import-not-found]


def test_build_step_prompt_includes_output_channel_delivery_requirement() -> None:
    state = TaskState(goal="Send weather update")
    prompt = _build_step_prompt(
        state=state,
        max_steps=5,
        output_channel="telegram_channel",
    )

    assert "Delivery requirement:" in prompt
    assert "telegram_channel" in prompt
    assert "send_to_channel(channel_id='telegram_channel'" in prompt
