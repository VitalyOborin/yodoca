# Soul Companion Event Namespace

This document defines the `companion.*` domain event namespace used by the
`soul` runtime. The namespace is intentionally separate from generic kernel
topics so channels, analytics, and future extensions can subscribe to companion
behavior as a first-class domain.

## Scope

These events are emitted by the `soul` extension through `context.emit(...)`.
They are durable EventBus topics intended for:

- Presence surfaces in channels and UI
- Observability and analytics
- Optional downstream indexing or journaling
- Loose coupling between `soul` and future extensions

The namespace does not replace `user.message`, `thread.completed`, or
MessageRouter events such as `user_message` and `agent_response`.

## Naming Rules

- All soul-owned domain events must begin with `companion.`
- Event names must describe a domain fact, not an internal method call
- Event payloads must be structured JSON-serializable dictionaries
- Payloads must be additive over time where practical
- Events must be emitted only for meaningful state changes, never every tick

## Canonical Events

### `companion.presence.updated`

Emitted when visible presence changes in a way the user or UI could reasonably
care about.

Payload:

```json
{
  "presence_state": "AMBIENT",
  "phase": "CURIOUS",
  "mood": 0.2,
  "updated_at": "2026-03-29T12:00:00Z"
}
```

Emission rules:

- Emit on presence state transition
- Do not emit on every tick when the effective visible state is unchanged

Primary consumers:

- Web presence surface
- CLI/Telegram lightweight status renderers

### `companion.phase.changed`

Emitted when the runtime changes behavioral phase.

Payload:

```json
{
  "old_phase": "AMBIENT",
  "new_phase": "REFLECTIVE",
  "trigger_drive": "reflection_need",
  "updated_at": "2026-03-29T12:05:00Z"
}
```

Emission rules:

- Emit exactly once per accepted phase transition

Primary consumers:

- Presence UI
- Debugging and metrics

### `companion.outreach.attempted`

Emitted when the companion initiates proactive outreach.

Payload:

```json
{
  "channel": "telegram_channel",
  "social_hunger": 0.81,
  "text_preview": "I was thinking about one thing...",
  "attempted_at": "2026-03-29T18:30:00Z"
}
```

Emission rules:

- Emit once per successful proactive send attempt
- `text_preview` must be truncated and safe for logs

Primary consumers:

- Metrics pipeline
- Audit/debugging surfaces

### `companion.outreach.result`

Emitted when an outreach is classified as `response`, `ignored`, or
`timing_miss`.

Payload:

```json
{
  "channel": "telegram_channel",
  "result": "response",
  "delay_seconds": 420,
  "resolved_at": "2026-03-29T18:37:00Z"
}
```

Emission rules:

- Emit once when the result is finalized
- Never emit duplicate outcomes for the same outreach window
- `response` means first inbound user message within the 60-minute response window
- `timing_miss` means no reply and low confidence that the user was available
- `ignored` means no reply despite sufficiently high estimated availability

Primary consumers:

- Initiative metrics
- Adaptive threshold logic

### `companion.reflection.created`

Emitted when a new reflection is created and persisted.

Payload:

```json
{
  "phase": "REFLECTIVE",
  "content_preview": "The user returns to purpose often.",
  "created_at": "2026-03-29T20:10:00Z"
}
```

Emission rules:

- Emit once per stored reflection
- Preview must be short and safe for logs

Primary consumers:

- Optional indexing or journaling
- Observability

### `companion.lifecycle.changed`

Emitted when the long-horizon soul lifecycle changes.

Payload:

```json
{
  "old_lifecycle_phase": "DISCOVERY",
  "new_lifecycle_phase": "FORMING",
  "updated_at": "2026-04-15T09:00:00Z"
}
```

Emission rules:

- Emit only on lifecycle transitions such as `DISCOVERY -> FORMING -> MATURE`

Primary consumers:

- UI explanations
- Metrics and research analysis

## Non-Goals

The following are intentionally not part of the namespace:

- Per-tick heartbeat events
- Raw drive dumps on every loop iteration
- Private implementation signals used only inside the extension
- MessageRouter internal callbacks (`user_message`, `agent_response`)

If a signal is needed only inside the extension, it should remain an internal
method call or in-memory event, not an EventBus topic.

## Versioning Guidance

When evolving payloads:

- Prefer additive fields
- Avoid renaming or removing existing keys without an ADR
- Keep field names explicit and domain-oriented

If a breaking payload change is unavoidable, introduce a new event name instead
of silently changing semantics.
