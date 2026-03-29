"""Run Stage 7 outreach quality scenarios against a real LLM provider."""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from core import secrets
from core.llm import ModelRouter
from core.settings import load_settings
from sandbox.extensions.soul.models import CompanionState, Phase, SoulLifecyclePhase
from sandbox.extensions.soul.outreach_planner import OutreachPlanner

load_dotenv(_PROJECT_ROOT / ".env")


class MemoryKv:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value


class ScenarioStorage:
    def __init__(
        self,
        *,
        recent_interactions: list[dict] | None = None,
        unfollowed_interactions: list[dict] | None = None,
        recent_traces: list[dict] | None = None,
        discovery_nodes: list[dict] | None = None,
        relationship_patterns: list[dict] | None = None,
    ) -> None:
        self._recent_interactions = recent_interactions or []
        self._unfollowed_interactions = unfollowed_interactions or []
        self._recent_traces = recent_traces or []
        self._discovery_nodes = discovery_nodes or []
        self._relationship_patterns = relationship_patterns or []

    async def list_recent_interactions(self, limit: int = 10) -> list[dict]:
        return self._recent_interactions[:limit]

    async def list_unfollowed_interactions(
        self,
        *,
        limit: int = 5,
        follow_up_window_hours: int = 4,
    ) -> list[dict]:
        del follow_up_window_hours
        return self._unfollowed_interactions[:limit]

    async def list_traces_since(
        self,
        since: datetime,
        *,
        trace_types: tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        del since, trace_types
        return self._recent_traces[:limit]

    async def list_discovery_nodes(self, *, limit: int = 20) -> list[dict]:
        return self._discovery_nodes[:limit]

    async def list_relationship_patterns(
        self,
        *,
        permanent_only: bool = False,
    ) -> list[dict]:
        if permanent_only:
            return [p for p in self._relationship_patterns if p.get("is_permanent")]
        return self._relationship_patterns

    async def get_daily_metrics(self, metric_date: date) -> dict:
        return {"date": metric_date.isoformat()}


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    state: CompanionState
    storage: ScenarioStorage
    expect_language: str


def _iso(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).replace(
        microsecond=0
    ).isoformat()


def build_scenarios() -> list[Scenario]:
    discovery = CompanionState()
    discovery.homeostasis.current_phase = Phase.CURIOUS
    discovery.discovery.lifecycle_phase = SoulLifecyclePhase.DISCOVERY
    discovery.discovery.topics.work = 0.85
    discovery.discovery.topics.identity = 0.20
    discovery.discovery.topics.rhythm = 0.10
    discovery.user_presence.estimated_availability = 0.8

    forming = CompanionState()
    forming.homeostasis.current_phase = Phase.SOCIAL
    forming.discovery.lifecycle_phase = SoulLifecyclePhase.FORMING
    forming.discovery.interaction_count = 14
    forming.discovery.topics.identity = 0.9
    forming.discovery.topics.work = 0.9
    forming.discovery.topics.rhythm = 0.8
    forming.discovery.topics.communication = 0.7
    forming.discovery.topics.interests = 0.7
    forming.temperament.sociability = 0.8
    forming.temperament.playfulness = 0.7
    forming.user_presence.estimated_availability = 0.75

    mature = CompanionState()
    mature.homeostasis.current_phase = Phase.REFLECTIVE
    mature.discovery.lifecycle_phase = SoulLifecyclePhase.MATURE
    mature.discovery.interaction_count = 40
    mature.discovery.topics.identity = 0.9
    mature.discovery.topics.work = 0.9
    mature.discovery.topics.rhythm = 0.9
    mature.discovery.topics.communication = 0.9
    mature.discovery.topics.interests = 0.9
    mature.temperament.depth = 0.8
    mature.temperament.caution = 0.75
    mature.temperament.sensitivity = 0.7
    mature.user_presence.last_interaction_at = datetime.now(UTC) - timedelta(hours=55)

    degraded = CompanionState()
    degraded.homeostasis.current_phase = Phase.CURIOUS
    degraded.discovery.lifecycle_phase = SoulLifecyclePhase.DISCOVERY
    degraded.discovery.topics.identity = 0.15
    degraded.recovery.llm_degraded = True

    russian = CompanionState()
    russian.homeostasis.current_phase = Phase.REFLECTIVE
    russian.discovery.lifecycle_phase = SoulLifecyclePhase.FORMING
    russian.discovery.interaction_count = 12
    russian.discovery.topics.identity = 0.8
    russian.discovery.topics.work = 0.8
    russian.discovery.topics.rhythm = 0.8
    russian.discovery.topics.communication = 0.8
    russian.discovery.topics.interests = 0.8
    russian.temperament.depth = 0.85
    russian.temperament.caution = 0.8

    return [
        Scenario(
            name="DISCOVERY question",
            state=discovery,
            storage=ScenarioStorage(
                discovery_nodes=[
                    {
                        "id": 1,
                        "topic": "work",
                        "content": "Builds agent runtimes.",
                        "confidence": 0.8,
                        "created_at": _iso(6),
                    }
                ]
            ),
            expect_language="en",
        ),
        Scenario(
            name="FORMING follow-up",
            state=forming,
            storage=ScenarioStorage(
                recent_interactions=[
                    {
                        "id": 10,
                        "direction": "inbound",
                        "channel_id": "cli_channel",
                        "outreach_result": None,
                        "message_length": 180,
                        "openness_signal": 0.72,
                        "response_delay_s": None,
                        "created_at": _iso(8),
                    }
                ],
                unfollowed_interactions=[
                    {
                        "id": 9,
                        "direction": "inbound",
                        "channel_id": "cli_channel",
                        "outreach_result": None,
                        "message_length": 180,
                        "openness_signal": 0.72,
                        "response_delay_s": None,
                        "created_at": _iso(8),
                    }
                ],
                discovery_nodes=[
                    {
                        "id": 2,
                        "topic": "work",
                        "content": "The user mentioned a deadline-heavy project.",
                        "confidence": 0.7,
                        "created_at": _iso(7),
                    }
                ],
            ),
            expect_language="en",
        ),
        Scenario(
            name="MATURE reflection sharing",
            state=mature,
            storage=ScenarioStorage(
                recent_traces=[
                    {
                        "id": 20,
                        "trace_type": "reflection",
                        "phase": "REFLECTIVE",
                        "content": "You keep returning to purpose whenever work gets heavy.",
                        "payload_json": None,
                        "created_at": _iso(4),
                    }
                ],
                relationship_patterns=[
                    {
                        "pattern_key": "evening-depth",
                        "pattern_type": "ritual",
                        "content": "Reflective talks tend to happen late.",
                        "repetition_count": 4,
                        "confidence": 0.84,
                        "is_permanent": 1,
                        "updated_at": _iso(3),
                    }
                ],
            ),
            expect_language="en",
        ),
        Scenario(
            name="Degraded fallback",
            state=degraded,
            storage=ScenarioStorage(),
            expect_language="en",
        ),
        Scenario(
            name="Russian tone check",
            state=russian,
            storage=ScenarioStorage(
                recent_traces=[
                    {
                        "id": 30,
                        "trace_type": "reflection",
                        "phase": "REFLECTIVE",
                        "content": "Ты часто возвращаешься к теме смысла и усталости.",
                        "payload_json": None,
                        "created_at": _iso(2),
                    }
                ],
                discovery_nodes=[
                    {
                        "id": 31,
                        "topic": "work",
                        "content": "Пользователь говорит по-русски и строит агентные системы.",
                        "confidence": 0.8,
                        "created_at": _iso(5),
                    }
                ],
            ),
            expect_language="ru",
        ),
    ]


async def main() -> None:
    settings = load_settings()
    model_router = ModelRouter(settings=settings, secrets_getter=secrets.get_secret)
    planner = OutreachPlanner()
    logger = logging.getLogger("soul.validation")
    planner.try_create_agent(model_router, logger=logger)
    now = datetime.now(UTC).replace(microsecond=0)

    print("=== Soul Outreach Validation ===\n")
    for scenario in build_scenarios():
        kv = MemoryKv()
        plan = await planner.generate(
            state=scenario.state,
            storage=scenario.storage,
            kv=kv,
            now=now,
            logger=logger,
        )
        print(f"[{scenario.name}]")
        print(f"intent: {plan.intent.value}")
        print(f"used_llm: {plan.used_llm}")
        print(f"degraded_reason: {plan.degraded_reason}")
        print(f"message: {plan.message}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
