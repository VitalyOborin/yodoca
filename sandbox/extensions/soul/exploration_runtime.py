"""Internal exploration runtime for the soul companion."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from agents import Agent, ModelSettings, Runner

from sandbox.extensions.soul.models import CompanionState, Phase
from sandbox.extensions.soul.storage import SoulStorage


class ExplorationRuntime:
    """Manages budgeted internal exploration in CURIOUS phase."""

    def __init__(self, *, max_per_day: int = 3) -> None:
        self._agent: Agent | None = None
        self._max_per_day = max_per_day

    @property
    def available(self) -> bool:
        return self._agent is not None

    def try_create_agent(self, model_router: Any, *, logger: logging.Logger) -> None:
        try:
            self._agent = Agent(
                name="SoulExplorationAgent",
                instructions=(
                    "Given the companion's own recent traces, produce one short novel observation. "
                    "Keep it under 18 words. No markdown, no quotes, no theatrics."
                ),
                model=model_router.get_model("soul"),
                model_settings=ModelSettings(parallel_tool_calls=False),
            )
        except Exception as exc:
            logger.warning("soul: exploration model unavailable: %s", exc)

    async def maybe_explore(
        self,
        *,
        now: datetime,
        state: CompanionState,
        storage: SoulStorage,
        kv: Any,
        logger: logging.Logger,
        trace_fn: Callable[..., Awaitable[None]],
        can_use_llm_fn: Callable[[], bool] | None = None,
        note_llm_call_fn: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if self._agent is None or state.homeostasis.current_phase is not Phase.CURIOUS:
            return
        if can_use_llm_fn is not None and not can_use_llm_fn():
            return
        if (
            await _get_daily_counter(kv, "soul.exploration.used", now)
            >= self._max_per_day
        ):
            return
        traces = await storage.list_traces_since(
            now - timedelta(days=7),
            trace_types=("reflection", "interaction", "phase_transition"),
            limit=8,
        )
        if len(traces) < 3:
            return

        prompt = _build_exploration_prompt(traces)
        try:
            result = await Runner.run(self._agent, prompt, max_turns=1)
        except Exception as exc:
            logger.debug("soul: exploration failed: %s", exc)
            return
        if note_llm_call_fn is not None:
            await note_llm_call_fn()
        observation = (result.final_output or "").strip().splitlines()[0][:160].strip()
        if not observation:
            return
        if not await _check_novelty(kv, state, observation):
            return
        await trace_fn(
            trace_type="exploration",
            content=observation,
            payload={"source_count": len(traces)},
            now=now,
        )
        await _increment_daily_counter(kv, "soul.exploration.used", now)

    def destroy(self) -> None:
        self._agent = None


def _build_exploration_prompt(traces: list[dict[str, Any]]) -> str:
    snippets = [f"- {item['trace_type']}: {item['content']}" for item in traces[:5]]
    return (
        "Recent internal traces:\n"
        + "\n".join(snippets)
        + "\nWrite one novel observation."
    )


async def _check_novelty(
    kv: Any,
    state: CompanionState,
    observation: str,
) -> bool:
    if kv is None:
        return True
    normalized = " ".join(observation.lower().split())
    previous = await kv.get("soul.exploration.last_observation")
    if normalized == (previous or ""):
        streak = int(await kv.get("soul.exploration.novelty_miss") or 0) + 1
        await kv.set("soul.exploration.novelty_miss", str(streak))
        if streak >= 3:
            state.homeostasis.curiosity = max(0.1, state.homeostasis.curiosity - 0.3)
        return False
    await kv.set("soul.exploration.last_observation", normalized)
    await kv.set("soul.exploration.novelty_miss", "0")
    return True


async def _get_daily_counter(kv: Any, prefix: str, now: datetime) -> int:
    if kv is None:
        return 0
    raw = await kv.get(f"{prefix}.{now.date().isoformat()}")
    return int(raw or 0)


async def _increment_daily_counter(kv: Any, prefix: str, now: datetime) -> int:
    if kv is None:
        return 0
    key = f"{prefix}.{now.date().isoformat()}"
    next_value = (await _get_daily_counter(kv, prefix, now)) + 1
    await kv.set(key, str(next_value))
    return next_value
