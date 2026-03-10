# ADR 028: Unified Sessions Table

## Status

Implemented

## Context

ADR 027 introduced `sessions` as the canonical metadata index for chat threads, alongside the OpenAI Agents SDK's `agent_sessions` table. Both tables tracked the same sessions, resulting in:

- Duplicate rows for every session (one in `agent_sessions`, one in `sessions`)
- A `_backfill_sessions` migration running on every startup to copy data from `agent_sessions` into `sessions`
- `sync_last_active_at` reading from both tables

The OpenAI Agents SDK's `SQLiteSession` accepts a `sessions_table` parameter, allowing it to use an existing table instead of creating its own `agent_sessions`.

## Decision

Use a single `sessions` table for both SDK and Yodoca:

1. **Unified schema** — Extend `sessions` with `updated_at` and defaults compatible with SDK inserts:
   - `channel_id TEXT NOT NULL DEFAULT 'unknown'` (SDK INSERT doesn't specify it)
   - `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
   - `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` (SDK writes this on every message)
   - `last_active_at INTEGER NOT NULL DEFAULT 0`

2. **Configure SDK** — Pass `sessions_table="sessions"` to all `SQLiteSession()` instantiations in `SessionManager`.

3. **Remove legacy code** — Delete `_backfill_sessions`, `_table_exists`, and all references to `agent_sessions`.

4. **Order of operations** — Call `_persist_session` before creating `SQLiteSession` so Yodoca writes the row first; SDK's insert becomes a no-op.

5. **No migration** — Clean break. Users delete old `session.db` for a fresh start.

## Consequences

Positive:

- Single source of truth for session metadata
- No startup backfill, simpler code
- `agent_sessions` table is never created

Negative:

- Existing `session.db` must be deleted; no backward compatibility
