"""Soul extension scaffold."""

from __future__ import annotations

from typing import Any


class SoulExtension:
    """Minimal lifecycle scaffold for the soul extension."""

    def __init__(self) -> None:
        self._ctx: Any = None
        self._started = False

    async def initialize(self, context: Any) -> None:
        self._ctx = context

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def destroy(self) -> None:
        self._ctx = None
        self._started = False

    def health_check(self) -> bool:
        return self._ctx is not None
