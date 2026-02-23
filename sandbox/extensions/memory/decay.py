"""Ebbinghaus decay and pruning. Phase 5."""

import math
import time


class DecayService:
    """Apply Ebbinghaus decay to semantic/procedural/opinion nodes."""

    def __init__(self, *, decay_threshold: float = 0.05) -> None:
        self._threshold = decay_threshold

    async def apply(self, storage: object) -> dict:
        """Apply decay and prune. Returns stats."""
        nodes = await storage.get_decayable_nodes()
        if not nodes:
            return {"decayed": 0, "pruned": 0}

        now = int(time.time())
        updates: list[tuple[str, float]] = []
        to_prune: list[str] = []

        for node in nodes:
            last_ts = node.get("last_accessed") or node.get("created_at")
            days_since = (now - last_ts) / 86400.0 if last_ts else 0.0
            decay_rate = node["decay_rate"]
            confidence = node["confidence"]

            confidence_new = confidence * math.exp(
                -decay_rate * (max(0, days_since) ** 0.8)
            )

            if confidence_new < self._threshold:
                to_prune.append(node["id"])
            else:
                updates.append((node["id"], confidence_new))

        if updates:
            await storage.batch_update_confidence(updates)
        if to_prune:
            await storage.soft_delete_nodes(to_prune)

        return {"decayed": len(updates), "pruned": len(to_prune)}
