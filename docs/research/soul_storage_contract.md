# Soul Storage Contract

This document defines the minimal storage contract for `soul.db` used in Stage
0 and Stage 1. The goal is to make write patterns, lifecycle expectations, and
cleanup rules explicit before extension implementation begins.

## Scope

This contract covers:

- `soul_state`
- `traces`
- `interaction_log`
- `soul_metrics`

It does not yet cover later-stage tables such as:

- `reflections`
- `relationship_patterns`
- `temperament_history`
- `consent_boundaries`

## Design Principles

### Soul owns behavioral state

`soul.db` is the source of truth for internal runtime behavior:

- drives
- phase and presence
- outreach history
- trace-like internal events
- aggregate metrics

### Memory owns long-term knowledge

`soul.db` is not a substitute for `memory.db`. It is intentionally optimized for
behavioral state and evaluation, not for semantic retrieval of long-lived user
facts.

### Writes must stay cheap and predictable

The background loop is expected to run for long periods. Database access must be
bounded and compatible with frequent snapshots and event logging.

## Table Responsibilities

### `soul_state`

Purpose:

- Stores the latest persisted runtime snapshot

Cardinality:

- Exactly one logical row (`id = 1`)

Write pattern:

- Update in place
- Write every 60 seconds at most in Stage 1
- Also write on meaningful lifecycle events such as phase transitions, user
  interaction, or outreach attempt/result

Read pattern:

- Read on startup for wake-up protocol
- Read on diagnostic tool requests

### `traces`

Purpose:

- Records meaningful short-lived internal events used for later reflection and
  exploration

Write pattern:

- Append only
- Never write per tick
- Only write on significant events such as:
  - phase transition
  - perception shift above threshold
  - outreach attempted/result
  - drive boundary hit
  - user interaction

Expected volume:

- Target 20-80 rows/day
- Significantly below the thousands/day produced by per-tick logging

Cleanup rule:

- TTL-based pruning by `created_at`
- Stage 0 target retention: 24-72 hours

### `interaction_log`

Purpose:

- Raw interaction facts for deriving availability and response patterns

Write pattern:

- Append one row per significant inbound or outbound interaction
- Store normalized fields needed for future aggregation:
  - direction
  - channel
  - hour/day slot
  - outreach result
  - response delay

Read pattern:

- Used for deriving interaction patterns in Stage 2 and Stage 3

Cleanup rule:

- Keep raw rows long enough to compute rolling windows
- Default retention can remain open during early research; if growth becomes a
  concern, archive or aggregate by date window

### `soul_metrics`

Purpose:

- Daily aggregate metrics for runtime evaluation and operational monitoring

Write pattern:

- Upsert by `date`
- Update counters incrementally during the day
- Maintain one current row per day

Read pattern:

- Diagnostics
- Evaluation gates
- Future self-correction logic

## Indexing Rules

- `created_at` indexes are mandatory for TTL cleanup and time-window queries
- Compound indexes should exist only where they support known read paths
- Avoid speculative indexes until later stages introduce concrete queries

## Concurrency and Durability

- SQLite must run in `WAL` mode
- Writes must be atomic at the application level
- Schema initialization must be idempotent
- The storage layer must support future multi-channel access without assuming a
  single writer forever

## Non-Goals

- Shared writes into `memory.db`
- Vector storage
- Full-text indexing in Stage 0
- Per-tick runtime journaling

## Adoption Notes

When Stage 1 creates the actual extension scaffold, `soul_schema.sql` should be
copied or moved into `sandbox/extensions/soul/schema.sql` without changing table
semantics unless a dedicated ADR or schema revision explicitly approves that
change.
