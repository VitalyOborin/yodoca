# Event Bus

A durable, SQLite-backed **pure event transport** for extension-to-agent flows and proactive notifications. This document describes the architecture, interfaces, and usage patterns for developers and architects.

**Principle: EventBus = pure transport.** `publish(topic, source, payload) → journal → deliver to subscribers`. No scheduling, no deferred logic. Use the Scheduler extension for time-based events.

---

## Overview

The Event Bus provides:

- **Durable publishing** — events are persisted to SQLite before delivery; no loss on process crash
- **At-least-once delivery** — events are marked `done` only after all handlers succeed; interrupted events are recovered on restart
- **Topic-based routing** — multiple subscribers per topic; handlers are invoked in registration order
- **Correlation** — optional `correlation_id` for tracing related events

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Extensions                                    │
│  ┌──────────────┐  ┌──────────────────┐                                 │
│  │ ctx.emit()   │  │ ctx.subscribe_   │                                 │
│  │              │  │ event()          │                                 │
│  └──────┬───────┘  └──────┬───────────┘                                 │
└─────────┼─────────────────┼─────────────────────────────────────────────┘
          │                 │
          ▼                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           EventBus (pure transport)                     │
│  publish() ──► journal.insert() ──► event_journal (pending)             │
│  subscribe() ──► in-memory handlers                                     │
└─────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     _dispatch_loop (single loop)                        │
│  1. Fetch pending from event_journal                                    │
│  2. Deliver to all subscribers; mark done/failed                        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Storage Schema

### `event_journal`


| Column         | Type | Description                                       |
| -------------- | ---- | ------------------------------------------------- |
| id             | int  | Primary key                                       |
| topic          | text | Event topic (e.g. `reminder.due`, `user.message`) |
| source         | text | Extension ID that published                       |
| payload        | text | JSON-serialized payload                           |
| correlation_id | text | Optional correlation for tracing                  |
| status         | text | `pending` → `processing` → `done` | `failed`      |
| created_at     | real | Unix timestamp                                    |
| processed_at   | real | Set when done/failed                              |
| error          | text | Error message if failed                           |

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

For time-based events (reminders, recurring tasks), use the **Scheduler extension** and its `schedule_once` / `schedule_recurring` tools. The Event Bus does not provide scheduling.

### `notify_user(text, channel_id=None)`

Send a message to the user. Internally emits `system.user.notify`. Guaranteed delivery via kernel handler.

### `request_agent_task(prompt, channel_id=None)`

Ask the Orchestrator to handle a task. Response goes to user. Emits `system.agent.task`.

### `request_agent_background(prompt, correlation_id=None)`

Trigger the Orchestrator silently; no user response. Emits `system.agent.background`.

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
        # Use Scheduler extension's schedule_once tool for delayed events
        pass  # agent schedules via schedule_once tool
```

---

## System Topics

Guaranteed topics that always have a kernel-registered handler. Use these when you need reliable delivery to the user or orchestrator.

| Topic | Payload | Handler |
| ----- | ------- | ------- |
| `system.user.notify` | `{text, channel_id?}` | Delivers message to user via active channel |
| `system.agent.task` | `{prompt, channel_id?, correlation_id?}` | Invokes Orchestrator; response to user |
| `system.agent.background` | `{prompt, correlation_id?}` | Invokes Orchestrator silently |

Use via `ctx.notify_user()`, `ctx.request_agent_task()`, `ctx.request_agent_background()`, or emit directly with `ctx.emit(SystemTopics.USER_NOTIFY, {...})`. The Scheduler extension uses these topics when the agent schedules reminders.

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
2. **Fetch** pending events from journal (limit 3 per iteration)
3. **Deliver** to all subscribers; mark `processing` → `done` or `failed`

Handlers run sequentially per event. If any handler raises, the event is marked `failed` with the error message; other handlers for that topic still run.

---

## Recovery

`recover()` is called once at startup (before `start()`):

1. Reset `processing` → `pending` (events interrupted by crash)
2. Log total recovered count

The dispatch loop then processes recovered events normally.

---

## Built-in Topics


| Topic             | Source      | Payload                         | Purpose                   |
| ----------------- | ----------- | ------------------------------- | ------------------------- |
| `user.message`    | cli_channel | `text`, `user_id`, `channel_id` | User input → agent        |
| `reminder.due`    | extensions  | `text`, optional `channel_id`   | Deferred reminders        |
| `checkin.started` | extensions  | `step`, `total`                 | Multi-step workflows      |
| `task.received`   | extensions  | `text`                          | Proactive task processing |


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

### Time-Based Events (Scheduler Extension)

For reminders, recurring tasks, or delayed events, use the **Scheduler extension** (`schedule_once`, `schedule_recurring` tools). The Scheduler runs its own tick loop, emits events via `ctx.emit()` when due, and is fully autonomous from the Event Bus.

---

## Design Decisions


| Decision              | Rationale                                                       |
| --------------------- | --------------------------------------------------------------- |
| Pure transport        | EventBus only publishes and delivers; no scheduling logic      |
| Scheduler extension   | Time-based events handled by autonomous extension               |
| Single dispatch loop  | One loop, one journal; simple and predictable                   |
| `poll_interval` + `_wake` | Balance between latency and CPU; `publish` wakes loop       |
| ExtensionContext only | Extensions never import core; single API surface                 |


---

## Configuration

Event Bus parameters are defined in `config/settings.yaml` under `event_bus:`:


| Key             | Default                         | Description                                                                       |
| --------------- | ------------------------------- | --------------------------------------------------------------------------------- |
| `db_path`       | `sandbox/data/event_journal.db` | Path to SQLite DB (relative to project root)                                      |
| `poll_interval` | `5.0`                           | Dispatch loop wait timeout in seconds; lower values reduce event latency |
| `batch_size`    | `3`                             | Max pending events fetched per loop iteration                                     |


---

## Observability

- **event_journal**: Query by `topic`, `status`, `correlation_id` for debugging
- Logs: `EventBus: recovered N events` at startup; handler exceptions logged with `subscriber_id` and `event_id`

