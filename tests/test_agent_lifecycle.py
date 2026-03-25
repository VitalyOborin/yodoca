"""Tests for core.agents.lifecycle periodic cleanup."""

import asyncio
from unittest.mock import MagicMock

import pytest

from core.agents.lifecycle import start_lifecycle_loop


@pytest.mark.asyncio
async def test_lifecycle_loop_calls_cleanup_periodically() -> None:
    registry = MagicMock()
    registry.cleanup_expired = MagicMock(return_value=0)

    task = start_lifecycle_loop(registry, interval_seconds=0.05)
    try:
        await asyncio.sleep(0.13)
        assert registry.cleanup_expired.call_count >= 2
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_lifecycle_loop_stops_on_cancel() -> None:
    registry = MagicMock()
    registry.cleanup_expired = MagicMock(return_value=0)

    task = start_lifecycle_loop(registry, interval_seconds=60.0)
    await asyncio.sleep(0)
    task.cancel()
    await task

    registry.cleanup_expired.assert_called_once()
