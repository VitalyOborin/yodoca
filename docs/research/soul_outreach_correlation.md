# Soul Outreach Correlation Contract

## Status

Accepted for Stage 2 implementation.

## Purpose

The `soul` runtime sends outreach via `ctx.notify_user()`, which is
fire-and-forget and does not provide a transport-level reply correlation ID.
Stage 2 therefore needs a local runtime contract for deciding whether an
outreach ended in:

- `response`
- `timing_miss`
- `ignored`

This document defines that contract so `S2-T4` and `S2-T5` can implement one
behavior instead of inventing per-call heuristics.

## Core Model

Each proactive outreach opens exactly one **outreach window**.

The runtime persists a `pending_outreach` record in soul state with:

- `outreach_id`
- `channel_id`
- `attempted_at`
- `availability_at_send`
- `window_deadline_at`
- `status = pending`

Rules:

- only one pending outreach window may exist at a time
- one outreach window may resolve to exactly one final result
- once resolved, the result is immutable

## Resolution Rules

### 1. `response`

Classify as `response` when:

- a `user_message` arrives on the same channel
- and it arrives within `60 minutes` after `attempted_at`

Persist:

- `result = response`
- `delay_seconds = user_message_at - attempted_at`

Effect:

- counts as successful contact
- clears pending outreach
- no cooldown penalty

### 2. `timing_miss`

Classify as `timing_miss` when:

- no user reply arrived within the 60-minute response window
- and the outreach was sent at a moment that should not be treated as a real
  rejection

This includes:

- `availability_at_send < 0.5`
- night / low-presence windows
- ambiguous cases where there is not enough evidence that the user actually saw
  the message

Effect:

- clears pending outreach
- does **not** apply ignored-cooldown
- increments `outreach_timing_miss`

Interpretation:

The companion does not treat this as rejection. It was simply a poor moment.

### 3. `ignored`

Classify as `ignored` when:

- no user reply arrived within the 60-minute response window
- and `availability_at_send >= 0.5`
- and no stronger signal exists that the miss was caused by timing

Effect:

- clears pending outreach
- applies ignored-cooldown
- increments `outreach_ignored`

Interpretation:

This is the only non-response outcome that should make the companion more
hesitant in the short term.

## Explicit Non-Goals

The following are **not** inferred in Stage 2:

- semantic intent of the user reply
- whether the reply was emotionally positive
- whether the user explicitly rejected the outreach

Stage 2 correlation is temporal and behavioral, not semantic.

## Edge Cases

### Reply after 60 minutes

If the first user reply arrives after the response window:

- the outreach is already resolved as `timing_miss` or `ignored`
- the later message is treated as a normal inbound interaction
- it must not retroactively rewrite the result

### Multiple user messages

Only the first inbound message that resolves the pending window is used for the
result. Later messages belong to the normal conversation flow.

### Second outreach attempt

The runtime must not send a second outreach while one is still pending.

### Restart during pending outreach

Pending outreach state is persisted. After restart:

- if the response window is still open, continue waiting
- if the response window has already expired, resolve immediately using the same
  availability-based rules

## Required Runtime Outputs

When an outreach resolves, the runtime must:

- persist the final result in `interaction_log` / metrics
- emit `companion.outreach.result`
- clear pending outreach state

When an outreach is attempted, the runtime must:

- persist the pending outreach record
- emit `companion.outreach.attempted`

## Summary

The contract is intentionally conservative:

- reply within 60 minutes -> `response`
- no reply + low availability -> `timing_miss`
- no reply + high availability -> `ignored`

This keeps the companion from overinterpreting silence as rejection.
