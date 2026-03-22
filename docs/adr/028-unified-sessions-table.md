# ADR 028: Unified Threads Table

## Status

Implemented

## Context

ADR 027 introduced `threads` as the canonical metadata index for chat threads, alongside the OpenAI Agents SDK's `agent_threads` table. Both tables tracked the same threads, resulting in:

- Duplicate rows for every session (one in `agent_threads`, one in `threads`)
- A `_backfill_threads` migration running on every startup to copy data from `agent_threads` into `threads`
- `sync_last_active_at` reading from both tables

The OpenAI Agents SDK's `SQLiteThread` accepts a `threads_table` parameter, allowing it to use an existing table instead of creating its own `agent_threads`.

## Decision

Use a single `threads` table for both SDK and Yodoca:

1. **Unified schema** — Extend `threads` with `updated_at` and defaults compatible with SDK inserts:
   - `channel_id TEXT NOT NULL DEFAULT 'unknown'` (SDK INSERT doesn't specify it)
   - `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
   - `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` (SDK writes this on every message)
   - `last_active_at INTEGER NOT NULL DEFAULT 0`

2. **Configure SDK** — Pass `threads_table="threads"` to all `SQLiteThread()` instantiations in `ThreadManager`.

3. **Remove legacy code** — Delete `_backfill_threads`, `_table_exists`, and all references to `agent_threads`.

4. **Order of operations** — Call `_persist_session` before creating `SQLiteThread` so Yodoca writes the row first; SDK's insert becomes a no-op.

5. **No migration** — Clean break. Users delete old `session.db` for a fresh start.

## Consequences

Positive:

- Single source of truth for thread metadata
- No startup backfill, simpler code
- `agent_threads` table is never created

Negative:

- Existing `session.db` must be deleted; no backward compatibility

