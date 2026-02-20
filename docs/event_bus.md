# Event Bus

A durable, SQLite-backed event bus for extension-to-agent flows, proactive notifications, and deferred scheduling. This document describes the architecture, interfaces, and usage patterns for developers and architects.

---

## Overview

The Event Bus provides:

- **Durable publishing** — events are persisted to SQLite before delivery; no loss on process crash
- **At-least-once delivery** — events are marked `done` only after all handlers succeed; interrupted events are recovered on restart
- **Deferred scheduling** — events can be scheduled to fire at a future timestamp
- **Topic-based routing** — multiple subscribers per topic; handlers are invoked in registration order
- **Correlation** — optional `correlation_id` for tracing related events

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Extensions                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │ ctx.emit()   │  │ ctx.schedule │  │ ctx.subscribe│                   │
│  │ ctx.schedule │  │ _at()        │  │ _event()     │                   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                   │
└─────────┼─────────────────┼─────────────────┼───────────────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           EventBus                                        │
│  publish() ──► journal.insert() ──► event_journal (pending)              │
│  schedule_at() ──► journal.schedule_deferred() ──► deferred_events       │
│  subscribe() ──► in-memory handlers                                       │
└─────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     _dispatch_loop (single loop)                          │
│  1. Promote due deferred_events → event_journal (pending)                │
│  2. Fetch pending from event_journal                                     │
│  3. Deliver to all subscribers; mark done/failed                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Storage Schema

### `event_journal`

| Column         | Type   | Description                                      |
|----------------|--------|--------------------------------------------------|
| id             | int    | Primary key                                      |
| topic          | text   | Event topic (e.g. `reminder.due`, `user.message`)|
| source         | text   | Extension ID that published                      |
| payload        | text   | JSON-serialized payload                          |
| correlation_id | text   | Optional correlation for tracing                |
| status         | text   | `pending` → `processing` → `done` \| `failed`   |
| created_at     | real   | Unix timestamp                                   |
| processed_at   | real   | Set when done/failed                             |
| error          | text   | Error message if failed                          |

### `deferred_events`

| Column         | Type   | Description                                      |
|----------------|--------|--------------------------------------------------|
| id             | int    | Primary key                                      |
| topic          | text   | Event topic                                      |
| source         | text   | Extension ID                                     |
| payload        | text   | JSON-serialized payload                          |
| correlation_id | text   | Optional                                         |
| fire_at        | real   | Unix timestamp when event should fire            |
| status         | text   | `scheduled` → `fired` \| `cancelled`             |
| created_at     | real   | Unix timestamp                                   |
| fired_at       | real   | Set when fired                                   |

Deferred events use a separate table because their lifecycle (`scheduled` → `fired`/`cancelled`) differs from the journal (`pending` → `done`/`failed`). When `fire_at <= now()`, they are promoted into `event_journal` as `pending` and processed by the same dispatch loop.

---

## ExtensionContext API

Extensions interact with the Event Bus **only** through `ExtensionContext` (passed to `initialize()`). No direct `EventBus` or `EventJournal` imports in extensions.

### `emit(topic, payload, correlation_id=None)`

Publish an event immediately. Fire-and-forget; returns when the event is written to the journal.

```python
await self._ctx.emit("task.received", {"text": "Summarize report"})
await self._ctx.emit("alert", {"level": "warning"}, correlation_id="req-123")
```

- **topic**: string (e.g. `reminder.due`, `checkin.started`)
- **payload**: dict (JSON-serializable)
- **source**: automatically set to `extension_id`

### `schedule_at(delay, topic, payload, correlation_id=None)`

Schedule an event to fire after `delay` seconds (or a `timedelta`). Returns `deferred_id` or `None` if no event bus.

```python
await self._ctx.schedule_at(5.0, "reminder.due", {"text": "Drink water"})
await self._ctx.schedule_at(timedelta(hours=2), "reminder.due", {"text": "Stand-up"})
```

- **delay**: `float` (seconds) or `timedelta`
- **topic**, **payload**, **correlation_id**: same as `emit`

### `subscribe_event(topic, handler)`

Register an async handler for a topic. Called from `initialize()`. Handler receives an `Event` (or equivalent dict-like object).

```python
async def initialize(self, context):
    self._ctx = context
    self._ctx.subscribe_event("checkin.started", self._on_checkin)

async def _on_checkin(self, event):
    step = event.payload.get("step", 1)
    total = event.payload.get("total", 3)
    if step < total:
        await self._ctx.schedule_at(8, "checkin.started", {"step": step + 1, "total": total})
```

---

## Manifest-Driven Subscriptions

The Loader wires handlers from `manifest.yaml` **before** extensions are initialized. Two built-in handlers:

### `handler: notify_user`

Sends `event.payload["text"]` to the user via the default channel.

```yaml
events:
  subscribes:
    - topic: alert.urgent
      handler: notify_user
```

### `handler: invoke_agent`

Invokes an `AgentProvider` extension with the event as context; sends the agent response to the user. Used for proactive agent flows (e.g. reminders, task processing).

```yaml
agent:
  integration_mode: tool
  model: gpt-5-mini
  instructions: |
    You handle reminders. When a reminder is due, respond with "Reminder: <text>"

events:
  subscribes:
    - topic: reminder.due
      handler: invoke_agent
```

### `handler: custom`

No automatic wiring. The extension must call `ctx.subscribe_event()` in `initialize()`.

---

## Event Model

Handlers receive an immutable `Event`:

```python
@dataclass(frozen=True)
class Event:
    id: int           # Journal row id
    topic: str
    source: str       # Extension ID that published
    payload: dict
    created_at: float # Unix timestamp
    correlation_id: str | None = None
    status: str       # "processing" during delivery
```

---

## Dispatch Loop

The Event Bus runs a single `_dispatch_loop`:

1. **Wait** for `_wake` or `poll_interval` timeout (default 5s)
2. **Promote** due deferred events: `fetch_due_deferred()` → `insert` into journal → `mark_deferred_fired`
3. **Fetch** pending events from journal (limit 3 per iteration)
4. **Deliver** to all subscribers; mark `processing` → `done` or `failed`

Handlers run sequentially per event. If any handler raises, the event is marked `failed` with the error message; other handlers for that topic still run.

---

## Recovery

`recover()` is called once at startup (before `start()`):

1. Reset `processing` → `pending` (events interrupted by crash)
2. Promote overdue deferred events into the journal
3. Log total recovered count

The dispatch loop then processes recovered events normally.

---

## Built-in Topics

| Topic          | Source        | Payload                          | Purpose                    |
|----------------|---------------|----------------------------------|----------------------------|
| `user.message` | cli_channel   | `text`, `user_id`, `channel_id`  | User input → agent         |
| `reminder.due` | extensions    | `text`, optional `channel_id`   | Deferred reminders         |
| `checkin.started` | extensions | `step`, `total`                  | Multi-step workflows       |
| `task.received`   | extensions | `text`                           | Proactive task processing  |

---

## Flow Examples

### Immediate Publish

```
Extension A: ctx.emit("task.received", {"text": "..."})
    → journal.insert() → status=pending
    → _wake.set()
    → dispatch loop fetches, delivers to subscribers
    → status=done
```

### Deferred Publish

```
Extension B: ctx.schedule_at(60, "reminder.due", {"text": "..."})
    → journal.schedule_deferred() → deferred_events, status=scheduled
    → _wake.set()
    → (60s later) dispatch loop: fetch_due_deferred → insert → mark_fired
    → dispatch loop fetches pending, delivers
    → status=done
```

### Chain of Deferred Events

```
checkin_trigger: emit checkin.started {step:1, total:3}
    → checkin_agent (invoke_agent): responds to user
    → checkin_processor (subscribe_event): schedule_at(8, checkin.started, {step:2, total:3})
    → (8s later) deferred fires → checkin.started {step:2}
    → repeat until step=3
```

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Separate `deferred_events` table | Different lifecycle; avoids mixing `scheduled` with `pending/done/failed` |
| Single dispatch loop | Deferred promotion is one SQL query per iteration; no extra thread/loop |
| Promote deferred → journal | Reuses retry, correlation, mark_done/failed; no special handling |
| `poll_interval` + `_wake` | Balance between latency and CPU; `schedule_at` wakes loop for near-term events |
| ExtensionContext only | Extensions never import core; single API surface |

---

## Configuration

- **DB path**: `sandbox/data/event_journal.db` (from runner)
- **Poll interval**: 5.0 seconds (EventBus default); can be reduced for faster deferred firing
- **Batch size**: 3 pending events per loop iteration (hardcoded)

---

## Observability

- **event_journal**: Query by `topic`, `status`, `correlation_id` for debugging
- **deferred_events**: Query `fire_at`, `status` to inspect scheduled work
- Logs: `EventBus: recovered N events` at startup; handler exceptions logged with `subscriber_id` and `event_id`
