"""DecayService: Ebbinghaus decay and pruning for facts."""

import logging
import math
import time
from typing import Any

from db import MemoryDatabase  # noqa: I001 - db loaded from ext dir via sys.path

logger = logging.getLogger(__name__)


class DecayService:
    """Apply Ebbinghaus decay to facts and prune low-confidence ones."""

    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    async def get_facts_for_decay(self) -> list[dict[str, Any]]:
        """Return active facts with decay_rate > 0, sorted oldest first."""
        conn = await self._db._ensure_conn()
        cursor = await conn.execute(
            """SELECT id, confidence, decay_rate, last_accessed, created_at
               FROM memories
               WHERE kind = 'fact'
                 AND valid_until IS NULL
                 AND decay_rate > 0
               ORDER BY COALESCE(last_accessed, created_at) ASC, created_at ASC""",
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "confidence": r[1],
                "decay_rate": r[2],
                "last_accessed": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]

    async def apply_decay_and_prune(
        self, threshold: float = 0.05
    ) -> dict[str, Any]:
        """
        Apply Ebbinghaus decay: confidence *= exp(-decay_rate * days_since_access).
        Soft-delete facts where new_confidence < threshold.
        Returns stats: {decayed, pruned, errors}.
        """
        facts = await self.get_facts_for_decay()
        now = int(time.time())
        decayed = 0
        pruned = 0
        errors: list[str] = []
        conn = await self._db._ensure_conn()

        for fact in facts:
            try:
                ref_ts = fact["last_accessed"] or fact["created_at"]
                days = max(0.0, (now - ref_ts) / 86400.0)
                new_conf = fact["confidence"] * math.exp(
                    -fact["decay_rate"] * days
                )
                new_conf = max(0.0, min(1.0, new_conf))

                if new_conf < threshold:
                    await conn.execute(
                        "UPDATE memories SET valid_until = ? WHERE id = ?",
                        (now, fact["id"]),
                    )
                    await conn.execute(
                        "DELETE FROM vec_memories WHERE memory_id = ?",
                        (fact["id"],),
                    )
                    await conn.execute(
                        "DELETE FROM memory_entities WHERE memory_id = ?",
                        (fact["id"],),
                    )
                    pruned += 1
                else:
                    await conn.execute(
                        """UPDATE memories
                           SET confidence = ?, last_accessed = ?
                           WHERE id = ?""",
                        (new_conf, now, fact["id"]),
                    )
                    decayed += 1
            except Exception as e:
                errors.append(f"fact {fact['id']}: {e}")
                logger.exception("Decay error for fact %s", fact["id"])

        await conn.commit()
        return {"decayed": decayed, "pruned": pruned, "errors": errors}
