"""Event Bus: durable event journal for extension↔agent flows and observability."""

from core.events.bus import EventBus
from core.events.models import Event

__all__ = ["Event", "EventBus"]
