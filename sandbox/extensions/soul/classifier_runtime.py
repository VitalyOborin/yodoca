"""Mood classifier runtime for trigger-based LLM perception correction."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agents import Agent, ModelSettings, Runner

from sandbox.extensions.soul.mood_classifier import (
    blend_with_heuristics,
    parse_classification_output,
    should_trigger_classification,
)

if TYPE_CHECKING:
    from sandbox.extensions.soul.models import CompanionState, PerceptionSignals
    from sandbox.extensions.soul.storage import SoulStorage


class ClassifierRuntime:
    """Encapsulates trigger-based LLM mood classification lifecycle.

    Owns the classifier agent, config, and task management.
    State mutation and persistence are delegated back to the caller
    via mutable ``state`` references and the ``persist_fn`` callback.
    """

    def __init__(
        self,
        *,
        daily_budget: int = 3,
        min_chars: int = 180,
        signal_threshold: float = 0.45,
        blend_weight: float = 0.25,
        max_concurrent: int = 2,
    ) -> None:
        self._agent: Agent | None = None
        self._daily_budget = daily_budget
        self._min_chars = min_chars
        self._signal_threshold = signal_threshold
        self._blend_weight = blend_weight
        self._max_concurrent = max_concurrent
        self._active_tasks: set[asyncio.Task[Any]] = set()

    @property
    def available(self) -> bool:
        return self._agent is not None

    @property
    def active_tasks(self) -> set[asyncio.Task[Any]]:
        return self._active_tasks

    def try_create_agent(self, model_router: Any, *, logger: logging.Logger) -> None:
        try:
            self._agent = Agent(
                name="SoulMoodClassifier",
                instructions=(
                    "Classify the emotional tone of one user message. "
                    "Return strict JSON with numeric fields "
                    "stress_signal, withdrawal_signal, openness_signal, "
                    "fatigue_signal, joy_signal, confidence. "
                    "Each field must be between 0.0 and 1.0. "
                    "Do not add prose or markdown."
                ),
                model=model_router.get_model("soul"),
                model_settings=ModelSettings(parallel_tool_calls=False),
            )
        except Exception as exc:
            logger.warning("soul: mood classifier model unavailable: %s", exc)

    async def maybe_schedule(
        self,
        *,
        text: str,
        heuristic: PerceptionSignals,
        now: datetime,
        state: CompanionState,
        storage: SoulStorage,
        logger: logging.Logger,
        persist_fn: Callable[[datetime], Awaitable[None]],
    ) -> None:
        if self._agent is None:
            return
        if not should_trigger_classification(
            text=text,
            heuristic=heuristic,
            min_chars=self._min_chars,
            signal_threshold=self._signal_threshold,
        ):
            return
        if len(self._active_tasks) >= self._max_concurrent:
            return

        metrics = await storage.get_daily_metrics(now.date())
        used_today = int((metrics or {}).get("inference_count") or 0)
        if used_today >= self._daily_budget:
            return

        task = asyncio.create_task(
            self._run(
                text=text,
                heuristic=heuristic,
                now=now,
                state=state,
                storage=storage,
                logger=logger,
                persist_fn=persist_fn,
            ),
            name=f"soul:mood-classifier:{now.isoformat()}",
        )
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _run(
        self,
        *,
        text: str,
        heuristic: PerceptionSignals,
        now: datetime,
        state: CompanionState,
        storage: SoulStorage,
        logger: logging.Logger,
        persist_fn: Callable[[datetime], Awaitable[None]],
    ) -> None:
        if self._agent is None:
            return

        prompt = (
            "User message:\n"
            f"{text}\n\n"
            "Heuristic signals:\n"
            f"- stress_signal: {heuristic.stress_signal:.2f}\n"
            f"- withdrawal_signal: {heuristic.withdrawal_signal:.2f}\n"
            f"- openness_signal: {heuristic.openness_signal:.2f}\n"
            f"- fatigue_signal: {heuristic.fatigue_signal:.2f}\n"
            f"- joy_signal: {heuristic.joy_signal:.2f}\n"
        )
        try:
            result = await Runner.run(self._agent, prompt, max_turns=1)
        except Exception as exc:
            logger.debug("soul: mood classifier failed: %s", exc)
            return

        classification = parse_classification_output(result.final_output or "")
        if classification is None or classification.confidence < 0.4:
            return

        state.perception = blend_with_heuristics(
            state.perception,
            classification,
            weight=self._blend_weight,
        )
        state.homeostasis.care_impulse = max(
            state.homeostasis.care_impulse,
            round(
                max(classification.stress_signal, classification.fatigue_signal)
                * classification.confidence,
                4,
            ),
        )
        await storage.upsert_daily_metrics(
            now.date(),
            inference_count=1,
            perception_corrections=1,
        )
        await persist_fn(now)

    async def stop(self) -> None:
        tasks = list(self._active_tasks)
        self._active_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def destroy(self) -> None:
        self._agent = None
