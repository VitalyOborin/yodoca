# Scheduler Extension

The **Scheduler** extension provides time-based event scheduling. It is a **ToolProvider** + **ServiceProvider** that stores one-shot and recurring schedules in SQLite and emits events to the Event Bus when they fire.

**Principle:** Event Bus is pure transport; it does not schedule. The Scheduler extension owns all time-based logic and publishes events when due.

---

## Overview

| Capability | Description |
|------------|-------------|
| **One-shot** | Fire an event once at a specific time (delay or ISO datetime) |
| **Recurring** | Fire an event on a cron schedule or fixed interval |
| **Tools** | `schedule_once`, `schedule_recurring`, `list_schedules`, `cancel_schedule`, `update_recurring_schedule` |

The Orchestrator (and other agents) use these tools to set reminders, recurring check-ins, and deferred notifications.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SchedulerExtension (ToolProvider + ServiceProvider)        │
│  _SchedulerStore: SQLite (one_shot_schedules, recurring_schedules)            │
└─────────────────────────────────────────────────────────────────────────────┘
          │                                    │
          │ get_tools()                         │ run_background()
          ▼                                    ▼
┌──────────────────────┐            ┌──────────────────────────────────────────┐
│  Orchestrator tools   │            │  Tick loop (every tick_interval seconds)  │
│  - schedule_once      │            │  1. fetch_due_one_shot()                  │
│  - schedule_recurring │            │  2. fetch_due_recurring()                │
│  - list_schedules     │            │  3. ctx.emit(topic, payload)               │
│  - cancel_schedule    │            │  4. mark fired / advance next_fire_at     │
│  - update_recurring   │            └──────────────────────────────────────────┘
└──────────────────────┘
```

**Storage:** `sandbox/data/scheduler/scheduler.db` (`context.data_dir / "scheduler.db"`) — SQLite with WAL + `synchronous=NORMAL`.

---

## Tools

### schedule_once

Schedule a one-shot event. Provide **exactly one** of `delay_seconds` or `at_iso`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `topic` | str | Event topic (e.g. `system.user.notify`, `system.agent.task`) |
| `payload_json` | str | JSON payload |
| `delay_seconds` | float | Seconds from now until fire |
| `at_iso` | str | ISO 8601 datetime (e.g. `2025-02-21T10:00:00`) |

**Payload contracts for system topics:**

| Topic | Key | Description |
|-------|-----|-------------|
| `system.user.notify` | `text` | Static message known at scheduling time |
| `system.agent.task` | `prompt` | Dynamic content; Orchestrator reasons at fire time |
| `system.agent.background` | `prompt` | Silent task; no user response |

### schedule_recurring

Create a recurring schedule. Provide **exactly one** of `cron` or `every_seconds`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `topic` | str | Event topic |
| `payload_json` | str | JSON payload |
| `cron` | str | Cron expression (e.g. `0 9 * * *` for daily at 09:00) |
| `every_seconds` | float | Interval in seconds |
| `until_iso` | str | Optional ISO 8601 end datetime |

### list_schedules

List all schedules. Optional `status` filter: `scheduled`, `fired`, `cancelled` (one-shot); `active`, `paused`, `cancelled` (recurring).

### cancel_schedule

Cancel a schedule by ID and type (`one_shot` or `recurring`).

### update_recurring_schedule

Update a recurring schedule: change `cron`, `every_seconds`, `until_iso`, or `status` (active/paused).

---

## Tick Loop

- **Interval:** `config.tick_interval` (default 30 seconds)
- **On `start()`:** Recover overdue recurring schedules (advance `next_fire_at` to future); fire any due one-shots immediately. Recurring schedules are **not** fired on startup — only advanced.
- **`run_background()` loop:** Sleep `tick_interval` → fetch due one-shots and recurring → emit events via `ctx.emit()` → mark fired / advance `next_fire_at`
- **Recurring expiry:** If `until_at` has passed, schedule is auto-cancelled in `advance_next()`

---

## Database Schema

### one_shot_schedules

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key (AUTOINCREMENT) |
| topic | TEXT | Event topic |
| payload | TEXT | JSON payload |
| fire_at | REAL | Unix timestamp |
| status | TEXT | `scheduled` → `fired` or `cancelled` |
| created_at | REAL | Unix timestamp |

### recurring_schedules

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key (AUTOINCREMENT) |
| topic | TEXT | Event topic |
| payload | TEXT | JSON payload |
| cron_expr | TEXT | Cron expression (or NULL) |
| every_sec | REAL | Interval seconds (or NULL) |
| until_at | REAL | End timestamp (or NULL) |
| status | TEXT | `active`, `paused`, `cancelled` |
| next_fire_at | REAL | Next fire timestamp |
| created_at | REAL | Unix timestamp |

---

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `config.tick_interval` | 30 | Seconds between tick loop iterations |

---

## Relation to Loader Cron Loop

The Loader has a separate **cron loop** for `SchedulerProvider` extensions (e.g. Heartbeat, memory_maintenance). That loop evaluates **manifest-defined** schedules (`schedules` in manifest.yaml) and calls `execute_task(task_name)`.

The **Scheduler extension** is different: it runs its own **ServiceProvider** tick loop and stores schedules in its database. It does **not** use the Loader's cron — it is fully autonomous.

---

## References

- [event_bus.md](event_bus.md) — Event Bus and system topics
- [extensions.md](extensions.md) — ToolProvider, ServiceProvider
