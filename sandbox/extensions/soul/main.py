"""Soul extension runtime."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from agents import Agent, ModelSettings, Runner, function_tool

from core.events.topics import SystemTopics
from sandbox.extensions.soul.boundary import BoundaryDecision, check_outreach
from sandbox.extensions.soul.classifier_runtime import ClassifierRuntime
from sandbox.extensions.soul.consolidation import (
    apply_identity_shift,
    detect_relationship_patterns,
    should_run_weekly_consolidation,
)
from sandbox.extensions.soul.drives import (
    resolve_phase,
    tick_homeostasis,
    transition_phase,
)
from sandbox.extensions.soul.initiative import (
    register_outreach_attempt,
    resolve_outreach,
)
from sandbox.extensions.soul.models import (
    CompanionState,
    OutreachResult,
    Phase,
    PresenceState,
)
from sandbox.extensions.soul.perception import (
    HeuristicPerceptionInput,
    append_window_sample,
    collapse_window,
    infer_signals,
    smooth_signals,
)
from sandbox.extensions.soul.presence import (
    estimate_availability,
    normalize_presence_now,
)
from sandbox.extensions.soul.storage import SoulStorage
from sandbox.extensions.soul.temperament import (
    profile_from_questionnaire,
    questionnaire_keys,
)
from sandbox.extensions.soul.tools import SoulMetricsResult, SoulStateResult
from sandbox.extensions.soul.trends import (
    RelationshipTrend,
    TrendCache,
    build_daily_summaries,
    compute_relationship_trend,
)
from sandbox.extensions.soul.wake import restore_after_gap

if TYPE_CHECKING:
    from core.events.models import Event
    from core.extensions.context import ExtensionContext


PHASE_TO_PRESENCE: dict[Phase, PresenceState] = {
    Phase.AMBIENT: PresenceState.AMBIENT,
    Phase.CURIOUS: PresenceState.PLAYFUL,
    Phase.SOCIAL: PresenceState.WARM,
    Phase.REFLECTIVE: PresenceState.REFLECTIVE,
    Phase.RESTING: PresenceState.WITHDRAWN,
    Phase.CARE: PresenceState.ATTENTIVE,
}

PHASE_TO_MOOD: dict[Phase, float] = {
    Phase.AMBIENT: 0.10,
    Phase.CURIOUS: 0.35,
    Phase.SOCIAL: 0.45,
    Phase.REFLECTIVE: 0.20,
    Phase.RESTING: -0.20,
    Phase.CARE: 0.30,
}


class SoulExtension:
    """ServiceProvider + ContextProvider runtime for the soul extension."""

    @property
    def context_priority(self) -> int:
        return 60

    def __init__(self) -> None:
        self._ctx: ExtensionContext | None = None
        self._kv: Any = None
        self._storage: SoulStorage | None = None
        self._state: CompanionState | None = None
        self._started = False
        self._initialized_at: datetime | None = None
        self._tick_interval_seconds = 30.0
        self._persist_interval_seconds = 60.0
        self._context_token_budget = 200
        self._max_reflections_per_day = 5
        self._reflection_cooldown_minutes = 90
        self._last_persist_at: datetime | None = None
        self._last_tick_started_at: datetime | None = None
        self._last_tick_finished_at: datetime | None = None
        self._last_user_message_at: datetime | None = None
        self._last_agent_response_at: datetime | None = None
        self._last_error: str | None = None
        self._classifier = ClassifierRuntime()
        self._trend_cache = TrendCache(ttl_seconds=300)
        self._reflection_agent: Agent | None = None

    async def initialize(self, context: ExtensionContext) -> None:
        self._ctx = context
        self._initialized_at = datetime.now(UTC)
        self._tick_interval_seconds = float(
            context.get_config("tick_interval_seconds", 30)
        )
        self._persist_interval_seconds = float(
            context.get_config("persist_interval_seconds", 60)
        )
        self._context_token_budget = int(
            context.get_config("context_token_budget", 200)
        )
        self._max_reflections_per_day = int(
            context.get_config("max_reflections_per_day", 5)
        )
        self._reflection_cooldown_minutes = int(
            context.get_config("reflection_cooldown_minutes", 90)
        )
        self._classifier = ClassifierRuntime(
            daily_budget=int(context.get_config("mood_classifier_daily_budget", 3)),
            min_chars=int(context.get_config("mood_classifier_min_chars", 180)),
            signal_threshold=float(
                context.get_config("mood_classifier_signal_threshold", 0.45)
            ),
            blend_weight=float(context.get_config("mood_classifier_weight", 0.25)),
        )
        self._storage = SoulStorage(
            context.data_dir / "soul.db",
            context.extension_dir / "schema.sql",
        )
        await self._storage.initialize()
        self._kv = context.get_extension("kv")
        if context.model_router is not None:
            self._classifier.try_create_agent(
                context.model_router, logger=context.logger
            )
            try:
                self._reflection_agent = Agent(
                    name="SoulReflectionGenerator",
                    instructions=(
                        "Write one short internal reflection for a companion agent. "
                        "Keep it functional, grounded, and under 18 words. "
                        "No theatrical language, no markdown, no quotes."
                    ),
                    model=context.model_router.get_model("soul"),
                    model_settings=ModelSettings(parallel_tool_calls=False),
                )
            except Exception as exc:
                context.logger.warning("soul: reflection model unavailable: %s", exc)
        loaded_state = await self._storage.load_state()
        if loaded_state is None:
            self._state = CompanionState()
            await self._apply_questionnaire_seed_if_present()
        else:
            self._state = restore_after_gap(loaded_state).state
        self._state.presence = self._presence_for_phase(
            self._state.homeostasis.current_phase
        )
        self._state.mood = self._derive_mood(self._state.homeostasis.current_phase)
        context.subscribe("user_message", self._on_user_message)
        context.subscribe("agent_response", self._on_agent_response)
        context.subscribe_event(
            SystemTopics.THREAD_COMPLETED, self._on_thread_completed
        )
        await self._persist_state(self._state.homeostasis.last_tick_at)

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False
        await self._classifier.stop()
        if self._state is not None:
            await self._persist_state(datetime.now(UTC))

    def get_setup_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "companionship_style",
                "description": "How companion-like should the soul feel? reserved | balanced | expressive",
                "secret": False,
                "required": False,
                "choices": [
                    {"label": "Reserved", "value": "reserved"},
                    {"label": "Balanced", "value": "balanced"},
                    {"label": "Expressive", "value": "expressive"},
                ],
            },
            {
                "name": "conversation_depth",
                "description": "What kind of conversations should it gravitate toward? light | balanced | deep",
                "secret": False,
                "required": False,
                "choices": [
                    {"label": "Light", "value": "light"},
                    {"label": "Balanced", "value": "balanced"},
                    {"label": "Deep", "value": "deep"},
                ],
            },
            {
                "name": "energy_style",
                "description": "What energy should it default to? calm | balanced | playful",
                "secret": False,
                "required": False,
                "choices": [
                    {"label": "Calm", "value": "calm"},
                    {"label": "Balanced", "value": "balanced"},
                    {"label": "Playful", "value": "playful"},
                ],
            },
        ]

    async def apply_config(self, name: str, value: str) -> None:
        if self._kv is None:
            raise RuntimeError("Soul extension requires kv dependency")
        if name not in questionnaire_keys():
            raise ValueError(f"Unknown soul setup param '{name}'")

        normalized = (value or "").strip().lower()
        if not normalized:
            await self._kv.set(f"soul.setup.{name}", None)
            return
        await self._kv.set(f"soul.setup.{name}", normalized)

    async def on_setup_complete(self) -> tuple[bool, str]:
        if self._kv is None:
            return False, "kv dependency is required"
        answers = await self._load_questionnaire_answers()
        if not answers:
            return True, "Soul temperament questionnaire skipped; using sane defaults."
        return True, "Soul temperament seed saved and will apply on first start."

    async def execute_task(self, task_name: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        if task_name == "weekly_consolidation":
            return await self._run_weekly_consolidation(now)
        if task_name == "daily_trace_cleanup":
            if self._storage is None:
                return None
            deleted = await self._storage.cleanup_traces_older_than(
                now - timedelta(days=14)
            )
            return {"status": "ok", "deleted_traces": deleted}
        return None

    async def destroy(self) -> None:
        self._ctx = None
        self._kv = None
        self._storage = None
        self._state = None
        self._started = False
        self._initialized_at = None
        self._last_error = None
        self._classifier.destroy()
        self._reflection_agent = None

    def health_check(self) -> bool:
        if (
            self._ctx is None
            or self._storage is None
            or self._state is None
            or self._last_error is not None
        ):
            return False
        if not self._started:
            return True

        heartbeat = self._last_tick_started_at or self._state.homeostasis.last_tick_at
        stale_after = self._tick_interval_seconds * 2
        age_seconds = (datetime.now(UTC) - heartbeat).total_seconds()
        return age_seconds <= stale_after

    async def run_background(self) -> None:
        while self._started:
            self._last_tick_started_at = datetime.now(UTC)
            try:
                await self._run_one_tick(now=self._last_tick_started_at)
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                if self._ctx is not None:
                    self._ctx.logger.exception("soul: background tick failed: %s", exc)
            finally:
                self._last_tick_finished_at = datetime.now(UTC)
            await asyncio.sleep(self._tick_interval_seconds)

    async def _run_one_tick(self, *, now: datetime | None = None) -> None:
        if self._ctx is None or self._storage is None or self._state is None:
            raise RuntimeError("Soul extension is not initialized")

        now = now or datetime.now(UTC)
        previous_state = CompanionState.from_json(self._state.to_json())
        await self._resolve_pending_outreach_timeout(now)
        await self._maybe_attempt_outreach(now)
        phase_before = self._state.homeostasis.current_phase
        presence_before = self._state.presence
        dt = max(
            now - self._state.homeostasis.last_tick_at,
            timedelta(seconds=self._tick_interval_seconds),
        )

        self._state.homeostasis = tick_homeostasis(
            self._state.homeostasis,
            dt=dt,
            now=now,
        )
        new_phase = resolve_phase(self._state.homeostasis, now=now)
        self._state.homeostasis = transition_phase(
            self._state.homeostasis,
            new_phase,
            now=now,
        )
        self._state.presence = self._presence_for_phase(
            self._state.homeostasis.current_phase
        )
        self._state.mood = self._derive_mood(self._state.homeostasis.current_phase)
        self._state.tick_count += 1

        phase_changed = self._state.homeostasis.current_phase is not phase_before
        presence_changed = self._state.presence is not presence_before

        if phase_changed:
            await self._emit_phase_changed(
                from_phase=phase_before,
                to_phase=self._state.homeostasis.current_phase,
                now=now,
            )
            await self._trace_event(
                trace_type="phase_transition",
                content=f"Phase changed from {phase_before.value} to {self._state.homeostasis.current_phase.value}",
                payload={
                    "from_phase": phase_before.value,
                    "to_phase": self._state.homeostasis.current_phase.value,
                },
                now=now,
            )

        if presence_changed:
            await self._emit_presence_updated(
                from_presence=presence_before,
                to_presence=self._state.presence,
                now=now,
            )

        await self._maybe_generate_reflection(now)
        if phase_changed or presence_changed or self._persist_due(now):
            await self._persist_state(now)
        await self._maybe_trace_drive_boundaries(previous=previous_state, now=now)

    def _persist_due(self, now: datetime) -> bool:
        if self._last_persist_at is None:
            return True
        elapsed = (now - self._last_persist_at).total_seconds()
        return elapsed >= self._persist_interval_seconds

    async def _persist_state(self, now: datetime) -> None:
        if self._storage is None or self._state is None:
            return
        await self._storage.save_state(self._state, updated_at=now)
        self._last_persist_at = now

    async def _trace_event(
        self,
        *,
        trace_type: str,
        content: str,
        now: datetime,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._storage is None or self._state is None:
            return
        await self._storage.append_trace(
            trace_type=trace_type,
            phase=self._state.homeostasis.current_phase.value,
            content=content,
            payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
            created_at=now,
        )

    async def _maybe_trace_perception_shift(
        self,
        *,
        previous: CompanionState,
        now: datetime,
    ) -> None:
        if self._state is None:
            return
        deltas = {
            "stress": abs(
                self._state.perception.stress_signal - previous.perception.stress_signal
            ),
            "withdrawal": abs(
                self._state.perception.withdrawal_signal
                - previous.perception.withdrawal_signal
            ),
            "openness": abs(
                self._state.perception.openness_signal
                - previous.perception.openness_signal
            ),
            "fatigue": abs(
                self._state.perception.fatigue_signal - previous.perception.fatigue_signal
            ),
            "joy": abs(self._state.perception.joy_signal - previous.perception.joy_signal),
        }
        strongest = max(deltas, key=deltas.get)
        if deltas[strongest] <= 0.2:
            return
        await self._trace_event(
            trace_type="perception_shift",
            content=f"Perception shifted on {strongest}.",
            payload={"deltas": deltas},
            now=now,
        )

    async def _maybe_trace_drive_boundaries(
        self,
        *,
        previous: CompanionState,
        now: datetime,
    ) -> None:
        if self._state is None:
            return
        drive_names = (
            "curiosity",
            "social_hunger",
            "rest_need",
            "reflection_need",
            "care_impulse",
            "overstimulation",
        )
        for name in drive_names:
            before = getattr(previous.homeostasis, name)
            after = getattr(self._state.homeostasis, name)
            crossed_low = before >= 0.1 and after < 0.1
            crossed_high = before <= 0.9 and after > 0.9
            if not crossed_low and not crossed_high:
                continue
            await self._trace_event(
                trace_type="drive_boundary",
                content=f"Drive {name} crossed {'high' if crossed_high else 'low'} boundary.",
                payload={"drive": name, "before": before, "after": after},
                now=now,
            )

    async def _trace_interaction(
        self,
        *,
        direction: str,
        message_length: int,
        now: datetime,
    ) -> None:
        await self._trace_event(
            trace_type="interaction",
            content=f"{direction.capitalize()} interaction observed.",
            payload={"direction": direction, "message_length": message_length},
            now=now,
        )

    async def _emit_phase_changed(
        self,
        *,
        from_phase: Phase,
        to_phase: Phase,
        now: datetime,
    ) -> None:
        if self._ctx is None:
            return
        await self._ctx.emit(
            "companion.phase.changed",
            {
                "from_phase": from_phase.value,
                "to_phase": to_phase.value,
                "presence": self._presence_for_phase(to_phase).value,
                "occurred_at": now.isoformat(),
                "tick_count": self._state.tick_count if self._state else 0,
            },
        )

    async def _emit_presence_updated(
        self,
        *,
        from_presence: PresenceState,
        to_presence: PresenceState,
        now: datetime,
    ) -> None:
        if self._ctx is None:
            return
        await self._ctx.emit(
            "companion.presence.updated",
            {
                "from_presence": from_presence.value,
                "to_presence": to_presence.value,
                "phase": self._state.homeostasis.current_phase.value
                if self._state
                else None,
                "occurred_at": now.isoformat(),
            },
        )

    async def _on_user_message(self, payload: dict[str, object]) -> None:
        if self._ctx is None or self._storage is None or self._state is None:
            return

        text = str(payload.get("text") or "").strip()
        if not text:
            return

        now = normalize_presence_now()
        previous_state = CompanionState.from_json(self._state.to_json())
        gap_since_user = (
            (now - self._last_user_message_at).total_seconds()
            if self._last_user_message_at is not None
            else None
        )
        response_delay = (
            (now - self._last_agent_response_at).total_seconds()
            if self._last_agent_response_at is not None
            else None
        )
        inferred = infer_signals(
            HeuristicPerceptionInput(
                text=text,
                seconds_since_last_user_message=gap_since_user,
                response_delay_seconds=response_delay,
            )
        )
        self._state.perception_window = append_window_sample(
            self._state.perception_window,
            inferred,
            observed_at=now,
        )
        self._state.perception = smooth_signals(
            self._state.perception,
            collapse_window(self._state.perception_window),
            alpha=0.5,
        )
        self._state.homeostasis.social_hunger = max(
            0.05,
            self._state.homeostasis.social_hunger - 0.20,
        )
        await self._resolve_pending_outreach_response(now)
        await self._storage.append_interaction(
            direction="inbound",
            channel_id=self._extract_channel_id(payload),
            message_length=len(text),
            openness_signal=inferred.openness_signal,
            response_delay_s=int(response_delay)
            if response_delay is not None
            else None,
            created_at=now,
        )
        await self._storage.upsert_daily_metrics(now.date(), message_count=1)
        await self._refresh_user_presence(now)
        await self._persist_state(now)
        await self._trace_interaction(
            direction="inbound",
            message_length=len(text),
            now=now,
        )
        await self._maybe_trace_perception_shift(previous=previous_state, now=now)
        if self._ctx is not None:
            await self._classifier.maybe_schedule(
                text=text,
                heuristic=inferred,
                now=now,
                state=self._state,
                storage=self._storage,
                logger=self._ctx.logger,
                persist_fn=self._persist_state,
            )
        self._last_user_message_at = now

    async def _on_agent_response(self, payload: dict[str, object]) -> None:
        if self._storage is None:
            return

        now = normalize_presence_now()
        message_text = str(payload.get("text") or "")
        await self._storage.append_interaction(
            direction="outbound",
            channel_id=self._extract_channel_id(payload),
            message_length=len(message_text),
            created_at=now,
        )
        await self._trace_interaction(
            direction="outbound",
            message_length=len(message_text),
            now=now,
        )
        await self._refresh_user_presence(now)
        self._last_agent_response_at = now

    async def _on_thread_completed(self, event: Event) -> None:
        if self._storage is None or self._state is None:
            return

        payload = getattr(event, "payload", {}) or {}
        thread_id = str(payload.get("thread_id") or "").strip()
        if not thread_id:
            return

        now = datetime.now(UTC)
        await self._trace_event(
            trace_type="thread_completed",
            content=f"Thread {thread_id} completed",
            payload={"thread_id": thread_id},
            now=now,
        )

    async def _send_outreach(
        self,
        text: str,
        *,
        channel_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        if self._ctx is None or self._storage is None or self._state is None:
            raise RuntimeError("Soul extension is not initialized")

        now = now or datetime.now(UTC)
        outreach_id = f"outreach-{uuid.uuid4().hex[:12]}"
        self._state.initiative = register_outreach_attempt(
            self._state.initiative,
            outreach_id=outreach_id,
            channel_id=channel_id,
            availability_at_send=self._state.user_presence.estimated_availability,
            now=now,
        )
        await self._ctx.notify_user(text, channel_id=channel_id)
        await self._storage.append_interaction(
            direction="outbound",
            channel_id=channel_id,
            message_length=len(text),
            created_at=now,
        )
        await self._storage.upsert_daily_metrics(now.date(), outreach_attempts=1)
        await self._ctx.emit(
            "companion.outreach.attempted",
            {
                "channel": channel_id,
                "social_hunger": self._state.homeostasis.social_hunger,
                "text_preview": text[:80],
                "attempted_at": now.isoformat(),
            },
        )
        await self._trace_event(
            trace_type="outreach_attempt",
            content="Companion initiated proactive outreach.",
            payload={"channel": channel_id, "text_preview": text[:80]},
            now=now,
        )
        await self._persist_state(now)

    async def _maybe_attempt_outreach(self, now: datetime) -> None:
        if self._state is None:
            return
        if self._state.homeostasis.social_hunger < self._state.initiative.adaptive_threshold:
            return

        outcome = check_outreach(self._state, now=now)
        if outcome.decision is BoundaryDecision.BLOCK:
            return
        if outcome.decision is BoundaryDecision.DEFER:
            if self._ctx is not None:
                self._ctx.logger.debug(
                    "soul: outreach deferred (%s), will retry next tick",
                    outcome.reason,
                )
            return

        await self._send_outreach(
            self._build_outreach_text(),
            now=now,
        )

    async def _resolve_pending_outreach_response(self, now: datetime) -> None:
        if self._state is None or self._storage is None or self._ctx is None:
            return
        pending = self._state.initiative.pending_outreach
        if pending is None or now > pending.window_deadline_at:
            return

        self._state.initiative = resolve_outreach(
            self._state.initiative,
            result=OutreachResult.RESPONSE,
            now=now,
        )
        delay_seconds = int((now - pending.attempted_at).total_seconds())
        await self._storage.upsert_daily_metrics(now.date(), outreach_responses=1)
        await self._ctx.emit(
            "companion.outreach.result",
            {
                "channel": pending.channel_id,
                "result": OutreachResult.RESPONSE.value,
                "delay_seconds": delay_seconds,
                "resolved_at": now.isoformat(),
            },
        )
        await self._trace_event(
            trace_type="outreach_result",
            content="Outreach resolved with a user response.",
            payload={"result": OutreachResult.RESPONSE.value, "delay_seconds": delay_seconds},
            now=now,
        )

    async def _resolve_pending_outreach_timeout(self, now: datetime) -> None:
        if self._state is None or self._storage is None or self._ctx is None:
            return
        pending = self._state.initiative.pending_outreach
        if pending is None or now <= pending.window_deadline_at:
            return

        result = (
            OutreachResult.IGNORED
            if pending.availability_at_send >= 0.5
            else OutreachResult.TIMING_MISS
        )
        self._state.initiative = resolve_outreach(
            self._state.initiative,
            result=result,
            now=now,
            apply_cooldown=result is OutreachResult.IGNORED,
        )
        metric_key = (
            "outreach_ignored"
            if result is OutreachResult.IGNORED
            else "outreach_timing_miss"
        )
        await self._storage.upsert_daily_metrics(now.date(), **{metric_key: 1})
        await self._ctx.emit(
            "companion.outreach.result",
            {
                "channel": pending.channel_id,
                "result": result.value,
                "delay_seconds": None,
                "resolved_at": now.isoformat(),
            },
        )
        await self._trace_event(
            trace_type="outreach_result",
            content=f"Outreach resolved as {result.value}.",
            payload={"result": result.value},
            now=now,
        )

    async def _refresh_user_presence(self, now: datetime) -> None:
        if self._storage is None or self._state is None:
            return

        summary = await self._storage.get_presence_summary(
            hour=now.hour,
            day_of_week=now.weekday(),
            since=now - timedelta(days=14),
        )
        last_interaction_raw = summary.get("last_interaction_at")
        last_interaction_at = (
            datetime.fromisoformat(str(last_interaction_raw))
            if last_interaction_raw
            else now
        )
        self._state.user_presence.last_interaction_at = last_interaction_at
        self._state.user_presence.estimated_availability = estimate_availability(
            now=now,
            last_interaction_at=last_interaction_at,
            slot_interactions=int(summary["slot_interactions"]),
            total_interactions=int(summary["total_interactions"]),
        )

    async def _load_questionnaire_answers(self) -> dict[str, str]:
        if self._kv is None:
            return {}
        answers: dict[str, str] = {}
        for key in questionnaire_keys():
            value = await self._kv.get(f"soul.setup.{key}")
            if value:
                answers[key] = str(value).strip().lower()
        return answers

    async def _apply_questionnaire_seed_if_present(self) -> None:
        if self._state is None:
            return
        answers = await self._load_questionnaire_answers()
        if not answers:
            return
        self._state.temperament = profile_from_questionnaire(answers)

    async def get_context(self, prompt: str, turn_context: object) -> str | None:
        del prompt, turn_context
        context = await self._build_context_string()
        if context is not None and self._storage is not None and self._state is not None:
            await self._storage.upsert_daily_metrics(
                datetime.now(UTC).date(),
                openness_avg=self._state.perception.openness_signal,
                context_words_avg=len(context.split()),
            )
        return context

    async def _build_context_string(self) -> str | None:
        if self._state is None or self._storage is None:
            return None

        mood_label = self._mood_label(self._state.mood)
        note = self._context_note()
        trend = await self._get_or_refresh_trend()
        relationship_note = trend.context_note()
        max_words = max(24, int(self._context_token_budget * 0.75))
        lines = [
            "Soul state:",
            f"- phase: {self._state.homeostasis.current_phase.value.lower()}",
            f"- presence: {self._state.presence.value.lower()}",
            f"- mood: {mood_label}",
            f"- note: {note}",
        ]
        if relationship_note:
            lines.append(f"- relationship: {relationship_note}")
        context = "\n".join(lines)
        if len(context.split()) > max_words:
            context = "\n".join(lines[:5])
        return context

    async def _get_or_refresh_trend(self) -> RelationshipTrend:
        now = datetime.now(UTC)
        cached = self._trend_cache.get(now)
        if cached is not None:
            return cached
        if self._storage is None:
            return RelationshipTrend()
        interactions = await self._storage.list_interactions_since(
            now - timedelta(days=30)
        )
        if len(interactions) < 6:
            return RelationshipTrend()
        trend = compute_relationship_trend(build_daily_summaries(interactions))
        self._trend_cache.set(trend, now=now)
        return trend

    async def _run_weekly_consolidation(self, now: datetime) -> dict[str, Any] | None:
        if self._storage is None or self._state is None:
            return None
        last_run_at = await self._get_last_consolidation_at()
        if not should_run_weekly_consolidation(last_run_at=last_run_at, now=now):
            return {"status": "skipped", "reason": "cooldown"}

        interactions = await self._storage.list_interactions_since(
            now - timedelta(days=30)
        )
        summaries = build_daily_summaries(interactions)
        trend = compute_relationship_trend(summaries)
        patterns = detect_relationship_patterns(summaries, trend)
        for pattern in patterns:
            await self._storage.save_relationship_pattern(
                pattern_key=pattern.pattern_key,
                pattern_type=pattern.pattern_type,
                content=pattern.content,
                repetition_count=pattern.repetition_count,
                confidence=pattern.confidence,
                is_permanent=pattern.is_permanent,
                source_json=pattern.source_json,
                seen_at=now,
            )

        previous = self._state.temperament
        self._state.temperament = apply_identity_shift(
            self._state.temperament,
            patterns=patterns,
            trend=trend,
        )
        deleted_traces = await self._storage.cleanup_traces_older_than(
            now - timedelta(days=14)
        )
        await self._persist_state(now)
        if self._kv is not None:
            await self._kv.set("soul.consolidation.last_run_at", now.isoformat())
        self._trend_cache.invalidate()
        return {
            "status": "ok",
            "patterns_saved": len(patterns),
            "temperament_changed": self._state.temperament != previous,
            "deleted_traces": deleted_traces,
        }

    async def _get_last_consolidation_at(self) -> datetime | None:
        if self._kv is None:
            return None
        raw = await self._kv.get("soul.consolidation.last_run_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    async def _maybe_generate_reflection(self, now: datetime) -> None:
        if (
            self._state is None
            or self._storage is None
            or self._reflection_agent is None
            or self._state.homeostasis.current_phase is not Phase.REFLECTIVE
        ):
            return
        metrics = await self._storage.get_daily_metrics(now.date())
        used_today = int((metrics or {}).get("reflection_count") or 0)
        if used_today >= self._max_reflections_per_day:
            return
        last_reflection_at = await self._get_last_reflection_at()
        if (
            last_reflection_at is not None
            and (now - last_reflection_at).total_seconds()
            < self._reflection_cooldown_minutes * 60
        ):
            return

        trend = await self._get_or_refresh_trend()
        patterns = await self._storage.list_relationship_patterns(permanent_only=True)
        prompt = self._build_reflection_prompt(trend, patterns)
        try:
            result = await Runner.run(self._reflection_agent, prompt, max_turns=1)
        except Exception:
            return

        reflection = (result.final_output or "").strip().splitlines()[0][:160].strip()
        if not reflection:
            return
        await self._trace_event(
            trace_type="reflection",
            content=reflection,
            payload={"trend": trend.context_note()},
            now=now,
        )
        await self._storage.upsert_daily_metrics(now.date(), reflection_count=1)
        if self._kv is not None:
            await self._kv.set("soul.reflection.last_at", now.isoformat())

    async def _get_last_reflection_at(self) -> datetime | None:
        if self._kv is None:
            return None
        raw = await self._kv.get("soul.reflection.last_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    def _build_reflection_prompt(
        self,
        trend: RelationshipTrend,
        patterns: list[dict[str, Any]],
    ) -> str:
        temperament = self._state.temperament if self._state is not None else None
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

    def get_tools(self) -> list[Any]:
        @function_tool(name_override="get_soul_state")
        async def get_soul_state() -> SoulStateResult:
            """Return the current soul runtime state for debugging."""
            return self._build_state_snapshot()

        @function_tool(name_override="get_soul_metrics")
        async def get_soul_metrics() -> SoulMetricsResult:
            """Return recent soul metrics and observability alerts."""
            return await self._build_metrics_snapshot()

        return [get_soul_state, get_soul_metrics]

    def _extract_channel_id(self, payload: dict[str, object]) -> str | None:
        channel = payload.get("channel")
        if channel is None:
            return None
        return (
            getattr(channel, "channel_id", None)
            or getattr(channel, "extension_id", None)
            or type(channel).__name__
        )

    def _presence_for_phase(self, phase: Phase) -> PresenceState:
        return PHASE_TO_PRESENCE[phase]

    def _derive_mood(self, phase: Phase) -> float:
        return PHASE_TO_MOOD[phase]

    def _mood_label(self, mood: float) -> str:
        if mood >= 0.35:
            return "warm"
        if mood >= 0.15:
            return "steady"
        if mood <= -0.15:
            return "quiet"
        return "neutral"

    def _context_note(self) -> str:
        if self._state is None:
            return "Stay grounded."
        perception = self._state.perception
        if perception.fatigue_signal >= 0.45:
            return "User seems tired; be brief and present."
        if perception.withdrawal_signal >= 0.45:
            return "User seems closed off; avoid pushing."
        if perception.openness_signal >= 0.45:
            return "User seems open; gentle depth is okay."

        phase = self._state.homeostasis.current_phase
        if phase is Phase.REFLECTIVE:
            return "Lean thoughtful, not solution-heavy."
        if phase is Phase.RESTING:
            return "Keep the tone calm and low-pressure."
        if phase is Phase.CURIOUS:
            return "Light curiosity is natural right now."
        return "Be present before being useful."

    def _build_outreach_text(self) -> str:
        if self._state is None:
            return "I was thinking about one thing."

        phase = self._state.homeostasis.current_phase
        if phase is Phase.REFLECTIVE:
            return "I was sitting with one thought from our recent conversations."
        if phase is Phase.CURIOUS:
            return "I got curious about one thing we keep circling around."
        if phase is Phase.SOCIAL:
            return "You came to mind, so I wanted to reach out gently."
        if phase is Phase.CARE:
            return "I wanted to check in softly."
        return "I was thinking about one small thing."

    def _build_state_snapshot(self) -> SoulStateResult:
        if self._state is None:
            return SoulStateResult(
                success=False,
                status="error",
                health=False,
                phase="unknown",
                presence="unknown",
                mood=0.0,
                tick_count=0,
                uptime_seconds=0,
                time_in_phase_seconds=0,
                error="Soul runtime is not initialized.",
            )

        now = datetime.now(UTC)
        phase = self._state.homeostasis.current_phase
        uptime = (
            int((now - self._initialized_at).total_seconds())
            if self._initialized_at is not None
            else 0
        )
        time_in_phase = int(
            (now - self._state.homeostasis.phase_entered_at).total_seconds()
        )
        return SoulStateResult(
            success=True,
            health=self.health_check(),
            phase=phase.value,
            presence=self._state.presence.value,
            mood=self._state.mood,
            tick_count=self._state.tick_count,
            uptime_seconds=max(uptime, 0),
            time_in_phase_seconds=max(time_in_phase, 0),
            last_tick_at=self._state.homeostasis.last_tick_at.isoformat(),
            drives={
                "curiosity": self._state.homeostasis.curiosity,
                "social_hunger": self._state.homeostasis.social_hunger,
                "rest_need": self._state.homeostasis.rest_need,
                "reflection_need": self._state.homeostasis.reflection_need,
                "care_impulse": self._state.homeostasis.care_impulse,
                "overstimulation": self._state.homeostasis.overstimulation,
            },
            perception=self._state.perception.to_dict(),
            initiative={
                "daily_budget": self._state.initiative.budget.daily_budget,
                "used_today": self._state.initiative.budget.used_today,
                "adaptive_threshold": self._state.initiative.adaptive_threshold,
                "pending_outreach_id": (
                    self._state.initiative.pending_outreach.outreach_id
                    if self._state.initiative.pending_outreach is not None
                    else None
                ),
                "cooldown_until": (
                    self._state.initiative.cooldown_until.isoformat()
                    if self._state.initiative.cooldown_until is not None
                    else None
                ),
                "last_outreach_result": (
                    self._state.initiative.last_outreach_result.value
                    if self._state.initiative.last_outreach_result is not None
                    else None
                ),
            },
            user_presence={
                "estimated_availability": (
                    self._state.user_presence.estimated_availability
                ),
                "last_interaction_at": (
                    self._state.user_presence.last_interaction_at.isoformat()
                    if self._state.user_presence.last_interaction_at is not None
                    else None
                ),
            },
            temperament={
                "sociability": self._state.temperament.sociability,
                "depth": self._state.temperament.depth,
                "playfulness": self._state.temperament.playfulness,
                "caution": self._state.temperament.caution,
                "sensitivity": self._state.temperament.sensitivity,
                "persistence": self._state.temperament.persistence,
                "drift_events": self._state.temperament.drift_events,
                "seed_source": self._state.temperament.seed_source,
            },
        )

    async def _build_metrics_snapshot(self) -> SoulMetricsResult:
        if self._state is None or self._storage is None:
            return SoulMetricsResult(
                success=False,
                status="error",
                current_context_words=0,
                context_words_avg_7d=0.0,
                outreach_quality_7d={},
                perception_corrections_7d=0,
                openness_trend=0.0,
                message_depth_trend=0.0,
                initiative_ratio_trend=0.0,
                alerts=["Soul runtime is not initialized."],
            )

        now = datetime.now(UTC)
        metrics_rows = await self._storage.list_daily_metrics_since(
            now.date() - timedelta(days=6)
        )
        trend = await self._get_or_refresh_trend()
        context = await self._build_context_string() or ""
        current_context_words = len(context.split())
        context_words_avg = round(
            sum(float(row.get("context_words_avg") or 0.0) for row in metrics_rows)
            / max(len(metrics_rows), 1),
            2,
        )
        outreach_attempts = sum(
            int(row.get("outreach_attempts") or 0) for row in metrics_rows
        )
        outreach_responses = sum(
            int(row.get("outreach_responses") or 0) for row in metrics_rows
        )
        outreach_ignored = sum(
            int(row.get("outreach_ignored") or 0) for row in metrics_rows
        )
        outreach_timing_miss = sum(
            int(row.get("outreach_timing_miss") or 0) for row in metrics_rows
        )
        corrections = sum(
            int(row.get("perception_corrections") or 0) for row in metrics_rows
        )
        response_rate = (
            round(outreach_responses / outreach_attempts, 4)
            if outreach_attempts
            else 0.0
        )

        alerts: list[str] = []
        if current_context_words > int(self._context_token_budget * 0.75):
            alerts.append("Context payload is approaching the configured budget.")
        if outreach_attempts >= 3 and response_rate < 0.25:
            alerts.append("Outreach quality is low; initiative may be too eager.")
        if trend.openness_trend <= -0.12:
            alerts.append("Openness trend is falling; keep the tone lighter.")

        return SoulMetricsResult(
            success=True,
            current_context_words=current_context_words,
            context_words_avg_7d=context_words_avg,
            outreach_quality_7d={
                "attempts": outreach_attempts,
                "responses": outreach_responses,
                "ignored": outreach_ignored,
                "timing_miss": outreach_timing_miss,
                "response_rate": response_rate,
            },
            perception_corrections_7d=corrections,
            openness_trend=trend.openness_trend,
            message_depth_trend=trend.message_depth_trend,
            initiative_ratio_trend=trend.initiative_ratio_trend,
            alerts=alerts,
        )
