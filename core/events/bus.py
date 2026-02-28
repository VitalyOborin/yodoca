"""Pure event transport: publish → journal → deliver to subscribers.
No scheduling, no deferred logic. Use the scheduler extension for time-based events."""

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Awaitable, Callable

from core.events.journal import EventJournal
from core.events.models import Event

logger = logging.getLogger(__name__)


class EventBus:
    """Durable event bus: publish to journal, dispatch loop delivers to handlers."""

    def __init__(
        self,
        db_path: Path,
        poll_interval: float = 5.0,
        batch_size: int = 3,
        max_retries: int = 3,
        busy_timeout: int = 5000,
        stale_timeout: float = 300.0,
    ) -> None:
        self._journal = EventJournal(db_path, busy_timeout=busy_timeout)
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._stale_timeout = stale_timeout
        self._wake = asyncio.Event()
        self._subscribers: dict[
            str, list[tuple[Callable[[Event], Awaitable[None]], str]]
        ] = defaultdict(list)
        self._dispatch_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._watchdog_interval = 30.0

    async def publish(
        self,
        topic: str,
        source: str,
        payload: dict,
        correlation_id: str | None = None,
    ) -> int:
        """Write event to the journal. Returns event id. Fire-and-forget for caller."""
        event_id = await self._journal.insert(topic, source, payload, correlation_id)
        self._wake.set()
        return event_id

    def subscribe(
        self,
        topic: str,
        handler: Callable[[Event], Awaitable[None]],
        subscriber_id: str,
    ) -> None:
        """Register handler in memory. Called at startup (from manifest wiring or context)."""
        self._subscribers[topic].append((handler, subscriber_id))

    async def start(self) -> None:
        """Start the dispatch loop and watchdog as asyncio Tasks."""
        self._stopped = False
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("EventBus dispatch loop started")

    async def stop(self) -> None:
        """Graceful shutdown: wait for current handlers to finish."""
        self._stopped = True
        self._wake.set()
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None
        await self._journal.close()
        logger.info("EventBus stopped")

    async def _watchdog_loop(self) -> None:
        """Periodically recover stale events stuck in 'processing'."""
        while not self._stopped:
            try:
                await asyncio.sleep(self._watchdog_interval)
            except asyncio.CancelledError:
                break
            if self._stopped:
                break
            try:
                reset_count, dead_count = await self._journal.recover_stale(
                    self._stale_timeout, self._max_retries
                )
                if reset_count or dead_count:
                    logger.info(
                        "EventBus watchdog: reset %d stale, dead-lettered %d",
                        reset_count,
                        dead_count,
                    )
            except Exception as e:
                logger.exception("EventBus watchdog failed: %s", e)

    async def recover(self) -> int:
        """Call once at startup. Reset 'processing' -> 'pending'."""
        count = await self._journal.reset_processing_to_pending()
        if count:
            logger.info("EventBus: recovered %d events", count)
        return count

    async def _dispatch_loop(self) -> None:
        """Main loop: wait for work, fetch pending, deliver to handlers."""
        while not self._stopped:
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self._poll_interval,
                )
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

            if self._stopped:
                break

            events = await self._journal.claim_pending(limit=self._batch_size)
            for (
                event_id,
                topic,
                source,
                payload,
                created_at,
                correlation_id,
                retry_count,
            ) in events:
                if self._stopped:
                    break
                await self._deliver(
                    Event(
                        id=event_id,
                        topic=topic,
                        source=source,
                        payload=payload,
                        created_at=created_at,
                        correlation_id=correlation_id,
                        status="processing",
                        retry_count=retry_count,
                    ),
                )

    async def _deliver(self, event: Event) -> None:
        """Deliver event to handlers; mark done or failed.
        Event was already claimed (status=processing) by claim_pending; no mark_processing here."""
        handlers = self._subscribers.get(event.topic, [])

        if not handlers:
            await self._journal.mark_done(event.id)
            return

        errors: list[str] = []
        for handler, subscriber_id in handlers:
            try:
                await handler(event)
            except Exception as e:
                errors.append(str(e))
                logger.exception(
                    "EventBus handler %s failed for event %s/%s: %s",
                    subscriber_id,
                    event.topic,
                    event.id,
                    e,
                )

        if errors:
            error_msg = "; ".join(errors)
            if event.retry_count < self._max_retries:
                await self._journal.mark_retry(event.id)
                logger.warning(
                    "EventBus: retrying event %s (attempt %d/%d): %s",
                    event.id,
                    event.retry_count + 1,
                    self._max_retries,
                    error_msg,
                )
            else:
                await self._journal.mark_dead_letter(event.id, error_msg)
                logger.error(
                    "EventBus: dead-lettered event %s after %d retries: %s",
                    event.id,
                    event.retry_count,
                    error_msg,
                )
        else:
            await self._journal.mark_done(event.id)
