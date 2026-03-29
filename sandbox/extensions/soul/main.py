"""Soul extension runtime."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from agents import function_tool

from core.events.topics import SystemTopics
from sandbox.extensions.soul.boundary import BoundaryDecision, check_outreach
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
    infer_signals,
    smooth_signals,
)
from sandbox.extensions.soul.presence import (
    estimate_availability,
    normalize_presence_now,
)
from sandbox.extensions.soul.storage import SoulStorage
from sandbox.extensions.soul.tools import SoulStateResult
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
        self._storage: SoulStorage | None = None
        self._state: CompanionState | None = None
        self._started = False
        self._initialized_at: datetime | None = None
        self._tick_interval_seconds = 30.0
        self._persist_interval_seconds = 60.0
        self._context_token_budget = 200
        self._last_persist_at: datetime | None = None
        self._last_tick_started_at: datetime | None = None
        self._last_tick_finished_at: datetime | None = None
        self._last_user_message_at: datetime | None = None
        self._last_agent_response_at: datetime | None = None
        self._last_error: str | None = None

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
        self._storage = SoulStorage(
            context.data_dir / "soul.db",
            context.extension_dir / "schema.sql",
        )
        await self._storage.initialize()
        loaded_state = await self._storage.load_state()
        if loaded_state is None:
            self._state = CompanionState()
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
        if self._state is not None:
            await self._persist_state(datetime.now(UTC))

    async def destroy(self) -> None:
        self._ctx = None
        self._storage = None
        self._state = None
        self._started = False
        self._initialized_at = None
        self._last_error = None

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
            await self._storage.append_trace(
                trace_type="phase_transition",
                phase=self._state.homeostasis.current_phase.value,
                content=f"Phase changed from {phase_before.value} to {self._state.homeostasis.current_phase.value}",
                created_at=now,
            )

        if presence_changed:
            await self._emit_presence_updated(
                from_presence=presence_before,
                to_presence=self._state.presence,
                now=now,
            )

        if phase_changed or presence_changed or self._persist_due(now):
            await self._persist_state(now)

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
        self._state.perception = smooth_signals(self._state.perception, inferred)
        self._state.homeostasis.social_hunger = max(
            0.05,
            self._state.homeostasis.social_hunger - 0.20,
        )
        await self._resolve_pending_outreach_response(now)
        await self._storage.append_interaction(
            direction="inbound",
            channel_id=self._extract_channel_id(payload),
            response_delay_s=int(response_delay)
            if response_delay is not None
            else None,
            created_at=now,
        )
        await self._storage.upsert_daily_metrics(now.date(), message_count=1)
        await self._refresh_user_presence(now)
        await self._persist_state(now)
        self._last_user_message_at = now

    async def _on_agent_response(self, payload: dict[str, object]) -> None:
        if self._storage is None:
            return

        now = normalize_presence_now()
        await self._storage.append_interaction(
            direction="outbound",
            channel_id=self._extract_channel_id(payload),
            created_at=now,
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
        await self._storage.append_trace(
            trace_type="thread_completed",
            phase=self._state.homeostasis.current_phase.value,
            content=f"Thread {thread_id} completed",
            created_at=now,
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

    async def get_context(self, prompt: str, turn_context: object) -> str | None:
        del prompt, turn_context
        if self._state is None:
            return None

        mood_label = self._mood_label(self._state.mood)
        note = self._context_note()
        max_words = int(self._context_token_budget * 0.75)
        context = (
            "Soul state:\n"
            f"- phase: {self._state.homeostasis.current_phase.value.lower()}\n"
            f"- presence: {self._state.presence.value.lower()}\n"
            f"- mood: {mood_label}\n"
            f"- note: {note}"
        )
        if len(context.split()) > max_words:
            return "\n".join(context.splitlines()[:4])
        return context

    def get_tools(self) -> list[Any]:
        @function_tool(name_override="get_soul_state")
        async def get_soul_state() -> SoulStateResult:
            """Return the current soul runtime state for debugging."""
            return self._build_state_snapshot()

        return [get_soul_state]

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
        )
