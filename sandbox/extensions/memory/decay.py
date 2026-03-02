"""Ebbinghaus decay on facts.confidence. Memory v3 Phase 5."""

import time
from typing import Any


class DecayService:
    """Ebbinghaus decay on facts.confidence via fact_access_log."""

    def __init__(
        self,
        storage: Any,
        *,
        lambda_: float = 0.1,
        threshold: float = 0.05,
    ) -> None:
        self._storage = storage
        self._lambda = lambda_
        self._threshold = threshold

    async def apply(self) -> tuple[int, int]:
        """Apply decay and expire low-confidence facts. Returns (facts_decayed, facts_expired)."""
        now = int(time.time() * 1000)
        decayed, expired = await self._storage.apply_fact_decay(
            self._lambda, self._threshold, now
        )
        await self._storage.compact_fact_access_log()
        return (decayed, expired)
