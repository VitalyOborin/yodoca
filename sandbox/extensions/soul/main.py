"""Soul extension runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.events.topics import SystemTopics
from sandbox.extensions.soul.drives import (
    resolve_phase,
    tick_homeostasis,
    transition_phase,
)
from sandbox.extensions.soul.models import CompanionState, Phase, PresenceState
from sandbox.extensions.soul.perception import (
    HeuristicPerceptionInput,
    infer_signals,
    smooth_signals,
)
from sandbox.extensions.soul.storage import SoulStorage
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

    def __init__(self) -> None:
        self._ctx: ExtensionContext | None = None
        self._storage: SoulStorage | None = None
        self._state: CompanionState | None = None
        self._started = False
        self._tick_interval_seconds = 30.0
        self._persist_interval_seconds = 60.0
        self._last_persist_at: datetime | None = None
        self._last_tick_started_at: datetime | None = None
        self._last_tick_finished_at: datetime | None = None
        self._last_user_message_at: datetime | None = None
        self._last_agent_response_at: datetime | None = None
        self._last_error: str | None = None

    async def initialize(self, context: ExtensionContext) -> None:
        self._ctx = context
        self._tick_interval_seconds = float(
            context.get_config("tick_interval_seconds", 30)
        )
        self._persist_interval_seconds = float(
            context.get_config("persist_interval_seconds", 60)
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

        now = datetime.now(UTC)
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
        await self._storage.append_interaction(
            direction="inbound",
            channel_id=self._extract_channel_id(payload),
            response_delay_s=int(response_delay)
            if response_delay is not None
            else None,
            created_at=now,
        )
        await self._storage.upsert_daily_metrics(now.date(), inference_count=1)
        await self._persist_state(now)
        self._last_user_message_at = now

    async def _on_agent_response(self, payload: dict[str, object]) -> None:
        if self._storage is None:
            return

        now = datetime.now(UTC)
        await self._storage.append_interaction(
            direction="outbound",
            channel_id=self._extract_channel_id(payload),
            created_at=now,
        )
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
