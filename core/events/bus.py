"""Event Bus: durable event journal with dispatch loop."""

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
    ) -> None:
        self._journal = EventJournal(db_path)
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._wake = asyncio.Event()
        self._subscribers: dict[str, list[tuple[Callable[[Event], Awaitable[None]], str]]] = (
            defaultdict(list)
        )
        self._dispatch_task: asyncio.Task[None] | None = None
        self._stopped = False

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

    async def schedule_at(
        self,
        fire_at: float,
        topic: str,
        payload: dict,
        source: str = "scheduler",
        correlation_id: str | None = None,
    ) -> int:
        """Schedule event to be emitted at a specific unix timestamp."""
        deferred_id = await self._journal.schedule_deferred(
            topic, source, payload, fire_at, correlation_id
        )
        self._wake.set()
        return deferred_id

    def subscribe(
        self,
        topic: str,
        handler: Callable[[Event], Awaitable[None]],
        subscriber_id: str,
    ) -> None:
        """Register handler in memory. Called at startup (from manifest wiring or context)."""
        self._subscribers[topic].append((handler, subscriber_id))

    async def start(self) -> None:
        """Start the dispatch loop as an asyncio Task."""
        self._stopped = False
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info("EventBus dispatch loop started")

    async def stop(self) -> None:
        """Graceful shutdown: wait for current handlers to finish."""
        self._stopped = True
        self._wake.set()
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None
        await self._journal.close()
        logger.info("EventBus stopped")

    async def recover(self) -> int:
        """Call once at startup. Reset 'processing' -> 'pending'; promote overdue deferred."""
        count = await self._journal.reset_processing_to_pending()
        due = await self._journal.fetch_due_deferred()
        for deferred_id, topic, source, payload, correlation_id in due:
            await self._journal.insert(topic, source, payload, correlation_id)
            await self._journal.mark_deferred_fired(deferred_id)
            count += 1
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

            due = await self._journal.fetch_due_deferred()
            for deferred_id, topic, source, payload, correlation_id in due:
                await self._journal.insert(topic, source, payload, correlation_id)
                await self._journal.mark_deferred_fired(deferred_id)

            events = await self._journal.fetch_pending(limit=self._batch_size)
            for event_id, topic, source, payload, created_at, correlation_id in events:
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
                    ),
                )

    async def _deliver(self, event: Event) -> None:
        """Deliver event to handlers; mark done or failed."""
        handlers = self._subscribers.get(event.topic, [])
        await self._journal.mark_processing(event.id)

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
            await self._journal.mark_failed(event.id, "; ".join(errors))
        else:
            await self._journal.mark_done(event.id)
