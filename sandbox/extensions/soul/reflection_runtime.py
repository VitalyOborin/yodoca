"""Reflection generator runtime for the soul companion."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from agents import Agent, ModelSettings, Runner

from sandbox.extensions.soul.models import CompanionState, Phase
from sandbox.extensions.soul.storage import SoulStorage
from sandbox.extensions.soul.trends import RelationshipTrend


class ReflectionRuntime:
    """Manages budgeted reflection generation in REFLECTIVE phase."""

    def __init__(
        self,
        *,
        max_per_day: int = 5,
        cooldown_minutes: int = 90,
    ) -> None:
        self._agent: Agent | None = None
        self._max_per_day = max_per_day
        self._cooldown_minutes = cooldown_minutes

    @property
    def available(self) -> bool:
        return self._agent is not None

    def try_create_agent(self, model_router: Any, *, logger: logging.Logger) -> None:
        try:
            self._agent = Agent(
                name="SoulReflectionGenerator",
                instructions=(
                    "Write one short internal reflection for a companion agent. "
                    "Keep it functional, grounded, and under 18 words. "
                    "No theatrical language, no markdown, no quotes."
                ),
                model=model_router.get_model("soul"),
                model_settings=ModelSettings(parallel_tool_calls=False),
            )
        except Exception as exc:
            logger.warning("soul: reflection model unavailable: %s", exc)

    async def maybe_generate(
        self,
        *,
        now: datetime,
        state: CompanionState,
        storage: SoulStorage,
        kv: Any,
        logger: logging.Logger,
        trend: RelationshipTrend,
        trace_fn: Callable[..., Awaitable[None]],
    ) -> None:
        if (
            self._agent is None
            or state.homeostasis.current_phase is not Phase.REFLECTIVE
        ):
            return
        metrics = await storage.get_daily_metrics(now.date())
        used_today = int((metrics or {}).get("reflection_count") or 0)
        if used_today >= self._max_per_day:
            return
        last_at = await _get_kv_datetime(kv, "soul.reflection.last_at")
        if (
            last_at is not None
            and (now - last_at).total_seconds() < self._cooldown_minutes * 60
        ):
            return

        patterns = await storage.list_relationship_patterns(permanent_only=True)
        prompt = _build_reflection_prompt(trend, patterns, state.temperament)
        try:
            result = await Runner.run(self._agent, prompt, max_turns=1)
        except Exception as exc:
            logger.debug("soul: reflection generation failed: %s", exc)
            return

        reflection = (result.final_output or "").strip().splitlines()[0][:160].strip()
        if not reflection:
            return
        await trace_fn(
            trace_type="reflection",
            content=reflection,
            payload={"trend": trend.context_note()},
            now=now,
        )
        await storage.upsert_daily_metrics(now.date(), reflection_count=1)
        if kv is not None:
            await kv.set("soul.reflection.last_at", now.isoformat())

    def destroy(self) -> None:
        self._agent = None


async def _get_kv_datetime(kv: Any, key: str) -> datetime | None:
    if kv is None:
        return None
    raw = await kv.get(key)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _build_reflection_prompt(
    trend: RelationshipTrend,
    patterns: list[dict[str, Any]],
    temperament: Any,
) -> str:
    top_patterns = [item["content"] for item in patterns[:2]]
    return (
        "Current phase: reflective\n"
        f"Trend note: {trend.context_note() or 'No strong trend.'}\n"
        f"Patterns: {top_patterns or ['None']}\n"
        "Temperament:"
        f" sociability={temperament.sociability if temperament else 0.5:.2f},"
        f" depth={temperament.depth if temperament else 0.5:.2f},"
        f" playfulness={temperament.playfulness if temperament else 0.5:.2f}\n"
        "Write one internal reflection."
    )
