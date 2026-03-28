"""Shared test configuration: ensure project root is on sys.path."""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class FakeSoulContext:
    """Minimal ExtensionContext stand-in for soul extension tests."""

    def __init__(
        self,
        tmp_path: Path,
        config: dict[str, Any] | None = None,
        *,
        data_subdir: str = "soul-data",
        logger_name: str = "test.soul",
    ) -> None:
        self._config = config or {}
        self.data_dir = tmp_path / data_subdir
        self.extension_dir = Path("sandbox/extensions/soul")
        self.logger = logging.getLogger(logger_name)
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[tuple[str, str | None]] = []
        self.router_subscriptions: dict[str, Any] = {}
        self.bus_subscriptions: dict[str, Any] = {}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    async def emit(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))

    async def notify_user(self, text: str, channel_id: str | None = None) -> None:
        self.notifications.append((text, channel_id))

    def subscribe(self, event: str, handler: Any) -> None:
        self.router_subscriptions[event] = handler

    def subscribe_event(self, topic: str, handler: Any) -> None:
        self.bus_subscriptions[topic] = handler


@pytest.fixture
def soul_context(tmp_path: Path) -> FakeSoulContext:
    return FakeSoulContext(tmp_path)
