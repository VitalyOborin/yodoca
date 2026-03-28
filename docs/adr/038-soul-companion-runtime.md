# ADR 038: Soul Companion Runtime

## Status

Accepted.

## Context

Yodoca currently provides a reactive agent runtime built from extensions,
channels, memory, tools, schedulers, and background services. The `soul`
initiative introduces a new requirement: the agent must behave as a persistent
companion with its own internal state, rhythm, initiative policy, and visible
presence between user messages.

This is not a thin feature on top of memory or the Orchestrator. It is a new
behavioral runtime that must remain compatible with the existing extension model
while keeping architectural boundaries explicit and testable.

Three questions must be fixed before implementation proceeds:

1. What architectural constraints define the `soul` subsystem?
2. How does `soul` interact with `memory` without coupling to storage internals?
3. How do dependencies, evaluation, and stage gates evolve across delivery
   stages?

## Decision

### 1. Soul is a separate subsystem, not a memory add-on

The `soul` runtime is implemented as its own extension with its own storage,
state model, and background loop.

The subsystem owns:

- homeostatic state and drives
- phase and presence state
- initiative policy and outreach history
- relationship metrics and trends
- reflections, traces, and self-monitoring data

The subsystem does not own:

- general episodic or semantic knowledge storage
- retrieval infrastructure for user/world facts
- vector storage or embedding generation

### 2. Soul-memory boundary is strict

The boundary between `soul` and `memory` is hard:

- `soul` must never read or write `memory.db` directly
- `soul` may only access memory through public extension APIs obtained via
  `context.get_extension("memory")`
- `memory` remains the source of truth for long-term knowledge retrieval
- `soul` remains the source of truth for internal behavioral state

Implication:

- soul reflections, traces, and runtime metrics are stored in `soul.db`
- if selected soul-derived artifacts need to become memory-visible, they are
  published through explicit APIs or event-driven integration, not shared
  storage access

### 3. Soul invariants are architectural constraints

The following invariants are mandatory for all stages of implementation:

- `Right to silence` — no activity is a valid state
- `Bounded subjectivity` — user interpretation is probabilistic, not
  categorical
- `Finite memory identity` — internal state is curated, not total logging
- `Relationship over utility` — companion behavior wins over assistant-like
  productivity behavior unless the user explicitly requests a utility mode
- `Graceful presence` — the subsystem must degrade quietly when LLM/network
  resources are unavailable

Associated anti-patterns are documented separately in
`docs/research/soul_anti_patterns.md`.

### 4. Domain events use the `companion.*` namespace

All soul-owned EventBus domain topics must use the `companion.*` namespace.

Canonical events:

- `companion.presence.updated`
- `companion.phase.changed`
- `companion.outreach.attempted`
- `companion.outreach.result`
- `companion.reflection.created`
- `companion.lifecycle.changed`

The event namespace is documented in
`docs/research/soul_event_namespace.md`.

### 5. Stage dependency contract is explicit

The manifest dependency contract evolves by delivery stage:

| Stage | `depends_on` |
|------|---------------|
| Stage 0-2 | `[kv]` |
| Stage 3-5 | `[kv, memory]` |
| Full scope default | `[kv, memory]` |

`embedding` is explicitly not a required dependency for the default delivery
path. If future soul behavior needs direct embedding access, it must be
introduced by a separate ADR.

### 6. Stage gates are mandatory

The following gates are mandatory and block downstream work:

- Stage 0 gate: simulation tests must pass before extension implementation
- Stage 1 gate: 24h soak validation must pass before controlled initiative
- Stage 2 gate: manual GO/NO-GO evaluation must pass before personality and
  discovery work

### 7. Evaluation is part of the architecture, not post-hoc QA

The soul runtime must produce enough structured telemetry to evaluate:

- outreach quality
- perception correction rate
- openness trend
- phase diversity
- inference economy
- storage growth
- inner loop uptime

These metrics are collected by `soul` itself and treated as first-class
operational outputs.

## Consequences

### Positive

- The subsystem boundary stays clear and enforceable.
- Soul can evolve independently from memory storage internals.
- Delivery can be staged without dependency ambiguity.
- Evaluation and gates reduce the risk of building a technically correct but
  experientially broken companion.

### Trade-offs

- Some useful soul artifacts will need explicit bridging to become memory-aware.
- Stage 3 introduces a real runtime dependency on `memory`, increasing
  integration complexity.
- Metrics and observability work become mandatory early, which increases Stage 0
  and Stage 1 effort.

### Non-goals

- Direct vector storage by `soul`
- Shared database tables between `soul` and `memory`
- Unlimited proactive behavior
- Emotion-first retention optimization

## References

- `docs/research/soul.md`
- `docs/research/soul_development.md`
- `docs/research/soul_dev_plan.md`
- `docs/research/soul_anti_patterns.md`
- `docs/research/soul_event_namespace.md`
- `docs/research/soul_outreach_correlation.md`
