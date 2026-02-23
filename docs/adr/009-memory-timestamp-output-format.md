# ADR 009: Human-Readable Timestamp Fields in Memory Tool Output

## Status

Accepted. Implemented.

## Context

Memory tools (`search_memory`, `get_timeline`) return `event_time` and `created_at` as Unix epoch integers (`int`). When an AI agent receives search results such as:

```json
{"event_time": 1771860227, "content": "..."}
```

It cannot interpret the timestamp as a human-readable date without an explicit conversion step. In practice the agent either omits the date entirely from its response, asks the user for a timezone, or hedges ("internal timestamp, not a human-readable date"). All three degrade the user experience.

The root cause is that epoch integers are internal storage primitives — correct for sorting, filtering, and indexing, but unsuitable as agent-facing output.

## Decision

Add four display-only fields alongside every existing `event_time` integer in `search_memory` results and the `get_timeline` `timestamp` field:

| Field | Example | Purpose |
| --- | --- | --- |
| `event_time_iso` | `2026-02-23T15:23:47+00:00` | RFC 3339 UTC — unambiguous, sortable |
| `event_time_local` | `2026-02-23 18:23:47 UTC+3` | Local wall-clock — human-friendly, suitable for display |
| `event_time_tz` | `UTC+3` | Timezone label for the local representation |
| `event_time_relative` | `3 hours ago` | Relative time — most natural for conversational agents |

### Timezone strategy

Local timezone is determined at runtime from the host system using `datetime.now().astimezone().tzinfo`. For a personal agent running on the user's machine this is the correct and sufficient approach — no configuration is required. The strategy is valid as long as the agent process runs in the same timezone as the user, which is the common case for locally-deployed assistants.

`event_time_iso` always uses UTC (`timezone.utc`) and includes the explicit `+00:00` suffix so the model can reason about it unambiguously.

### Backward compatibility

- Existing integer fields (`event_time`, `created_at`) are never removed or altered.
- New fields are added purely as additional dict keys.
- Existing storage, retrieval, filtering, sorting, and decay logic is unchanged.
- No database schema changes.

### Helper location

Formatting logic lives in `core/utils/formatting.py` — a shared utility module available to all extensions. The `humanize` library (pure Python, ~50 KB, zero transitive deps) is used under the hood for `event_time_relative` via `humanize.naturaltime()` and will serve future formatting needs (`naturalsize` for bytes, `intcomma` for numbers, etc.).

Memory's `tools.py` imports `format_event_time` from `core.utils.formatting` and calls it in `search_memory` and `get_timeline`.

`get_timeline`'s existing `TimelineEvent.timestamp` field is updated to use the ISO format via the helper (previously it used `time.localtime` with a different pattern — `%Y-%m-%d %H:%M`).

### Fallback

If `event_time` is `None`, `0`, or a non-positive integer, all four display fields are set to `""` to signal "no timestamp available" without raising.

## Consequences

- The AI agent can directly quote dates from tool results without ambiguity or follow-up questions about timezone.
- `event_time_relative` is the most useful field for conversational context — the agent can say "you mentioned this 2 days ago" without any computation.
- No performance impact — the helper is pure CPU, O(1), negligible.
- `get_timeline` output changes: `timestamp` now carries ISO format (`2026-02-23T15:23:47+00:00`) instead of `%Y-%m-%d %H:%M` local format. This is a minor breaking change for any consumer parsing that field's format, but no such consumer exists in the current codebase.
- `core/utils/formatting.py` is available to all extensions — no duplication when heartbeat, scheduler, or other extensions need human-friendly formatting.
- New external dependency: `humanize>=4.9.0` (pure Python, ~50 KB).
