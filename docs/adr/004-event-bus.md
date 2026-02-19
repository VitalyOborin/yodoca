# ADR 004: Event Bus in Core

## Status

Proposed.

## Context

ADR 002 chose **direct callbacks** over an Event Bus to keep the nano-kernel simple: channels call `on_user_message`, the scheduler returns `dict` and the kernel calls `notify_user`. Internal pub/sub in `MessageRouter` (`subscribe` / `_emit`) exists only for middleware-style hooks (`user_message`, `agent_response`) and is in-memory, not persisted.

As the system grows, three needs emerge that currently would require three different mechanisms:

1. **Extensions ↔ Agents** — A scheduler (or other extension) emits an event (e.g. `tick`, `email.received`); an agent-extension or the kernel should react. Today this is done via `execute() → return {"text": "..."}` and `notify_user`. There is no generic "event → handler" path with persistence.
2. **Proactive agent loop** — The kernel should be able to subscribe to events and decide what to do (e.g. invoke an agent, notify the user). That implies a single place where "something happened" is recorded and dispatched.
3. **Observability** — Debugging stochastic LLM behaviour and auditing "what happened" requires a durable log of events. Without a central event store, this would be a separate audit mechanism.

Introducing a single **Event Bus** in the kernel addresses all three with one abstraction: one transport, one API, and the same event stream usable for dispatch and for audit. The implementation lives under `core/events/` and is **additive**: it sits alongside the existing MessageRouter and direct callbacks; it does not replace them.

## Decision

### 1. Role of the Event Bus

- **Single bus** for all asynchronous, event-style interaction inside the system.
- **Publish**: write to a durable journal (fire-and-forget for the publisher).
- **Consume**: a dispatch loop reads from the journal and invokes registered handlers; handlers are registered at startup (from manifests and/or `context.emit()`).
- **Journal = queue = audit log**: one store serves persistence, delivery, and observability.
- **Delivery guarantee: at-least-once.** Events persist before dispatch; `recover()` re-queues interrupted events on restart. Handlers must be idempotent (or implement their own deduplication by `event.id`).

Existing reactive path (user message → agent → channel) and `notify_user` remain as today. Event Bus is used for event-driven flows (e.g. scheduler → event → agent, or extension A → event → extension B / kernel).

### 2. Transport: SQLite journal

One database, one table: the journal is the queue and the audit log.

```sql
CREATE TABLE IF NOT EXISTS event_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id  TEXT,                    -- links causal chains (e.g. tick → agent_run → notify)
    topic           TEXT    NOT NULL,
    source          TEXT    NOT NULL,        -- extension_id of publisher
    payload         TEXT    NOT NULL,        -- JSON
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending | processing | done | failed
    created_at      REAL    NOT NULL,        -- unix timestamp
    processed_at    REAL,
    error           TEXT                     -- reason if failed
);

CREATE INDEX IF NOT EXISTS idx_ej_topic_status ON event_journal(topic, status);
CREATE INDEX IF NOT EXISTS idx_ej_status_created ON event_journal(status, created_at);
CREATE INDEX IF NOT EXISTS idx_ej_correlation ON event_journal(correlation_id);
```

The database connection must enable WAL mode on first open:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
```

WAL gives concurrent readers with a single writer — appropriate for one dispatch loop writing status updates while other code may read the journal for debugging or observability.

Rationale for SQLite in this codebase:

- No extra services; the app stays runnable as `python -m core`.
- Events survive process restart; `pending` (and recovered `processing`) are processed by the dispatch loop after startup.
- The table is the audit log by construction; no separate logging layer needed for event flow.
- If a different transport (e.g. Redis) is needed later, only the Event Bus implementation behind the same public API changes; extensions and kernel callers do not.

### 3. Event model

Events are generic: topic + source + payload. No typed event classes in the kernel at this stage.

```python
# core/events/models.py

@dataclass(frozen=True)
class Event:
    id: int
    topic: str                           # e.g. "email.received", "tick", "user.notify"
    source: str                          # extension_id of publisher
    payload: dict
    created_at: float                    # unix timestamp
    correlation_id: str | None = None    # causal chain link
    status: str = "pending"
```

Handlers receive an `Event` with the fields above. The full journal row (including `processed_at`, `error`) is available in the database for audit and debugging but is not exposed to handlers — they only need the event data.

The contract between publisher and subscriber is the **topic** (and optionally payload shape). Payload validation (e.g. via Pydantic schemas per topic) can be added later without changing the Event Bus API.

`correlation_id` enables tracing causal chains across events (e.g. `email.received` → `agent_invoked` → `user.notify`). A publisher can set it explicitly; if omitted, the bus does not auto-generate one. This aligns with ADR 003's `AgentInvocationContext.correlation_id`.

### 4. EventBus public API

```python
# core/events/bus.py

class EventBus:

    async def publish(
        self,
        topic: str,
        source: str,
        payload: dict,
        correlation_id: str | None = None,
    ) -> int:
        """Write event to the journal. Returns event id. Fire-and-forget for caller."""

    def subscribe(
        self,
        topic: str,
        handler: Callable[[Event], Awaitable[None]],
        subscriber_id: str,
    ) -> None:
        """Register handler in memory. Called at startup (from manifest wiring or context)."""

    def unsubscribe(self, topic: str, subscriber_id: str) -> None:
        """Remove subscription (e.g. for cleanup or dynamic unregister)."""

    async def start(self) -> None:
        """Start the dispatch loop as an asyncio Task."""

    async def stop(self) -> None:
        """Graceful shutdown: wait for current handlers to finish."""

    async def recover(self) -> int:
        """Call once at startup. Reset 'processing' → 'pending'. Return count reset."""
```

Design choices:

- **publish** only writes to the DB; it does not call handlers. This decouples publishers from handler latency and ensures the event is persisted even if a handler fails later. After the INSERT, `publish` signals the dispatch loop via an internal `asyncio.Event` so it wakes up immediately instead of waiting for the next poll interval.
- **subscribe** is in-memory only; subscriptions are not stored in the DB. They are re-registered on every process start from manifests and extension code (subscriptions as code, not data).
- **subscriber_id** identifies who is handling (for unsubscribe and for logging/observability).

### 5. Dispatch loop

A single asyncio Task runs a loop:

1. **Wait for work**: `await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)`. The `_wake` event is set by `publish()`, so events are picked up immediately in the common case. The timeout (e.g. 5 seconds) is a safety net for any edge cases and recovery scenarios.
2. **Claim a batch**: Fetch up to N (e.g. 10) `pending` events and atomically mark them `processing` in one transaction. This prevents double-delivery even if multiple workers were added later:

```sql
BEGIN IMMEDIATE;
UPDATE event_journal SET status = 'processing'
    WHERE id IN (SELECT id FROM event_journal WHERE status = 'pending' ORDER BY created_at LIMIT 10);
-- then SELECT the updated rows
COMMIT;
```

3. **Deliver**: For each claimed event, call all handlers registered for its topic. Handlers for a single event run **sequentially** (no race conditions between handlers of the same event). Multiple events in the batch run **concurrently** via `asyncio.gather`.
4. **Mark outcome**: `done` if all handlers succeeded; `failed` with error text if any handler raised. If a handler raises, remaining handlers for the same event still execute.
5. **Loop**.

**Ordering**: Events within a batch are dispatched concurrently. If strict FIFO ordering per topic is required (e.g. ordered processing of `email.received`), the handler itself must enforce it, or a future enhancement could add per-topic serial dispatch. For MVP, concurrent batch processing is sufficient — most topics are independent.

### 6. Failure handling

Events that fail are marked `failed` with the error description and stay in the journal. **No automatic retry in MVP.** Rationale:

- Most handler errors in this system are either bugs (should be fixed in code) or transient LLM failures (retrying immediately is unlikely to help; the cron/scheduler will produce the next event on its own schedule).
- Adding retry with backoff (`attempts`, `max_attempts`, `next_attempt_at`) is a natural extension of the schema and the dispatch loop, and can be added when a concrete use case demands it.
- `failed` events remain in the journal for debugging and audit. They can be manually re-queued (set `status = 'pending'`) via a tool or admin command if needed.

Future options (not in MVP): configurable retry policy per topic, dead-letter status for events that exceed max attempts, alerting on `failed` event count.

### 7. Manifest: `events` section

Manifests gain an optional `events` section for declaration and documentation of pub/sub:

```yaml
# Publisher example (e.g. email_checker)
events:
  publishes:
    - topic: email.received
      description: "Fires when a new email arrives in the inbox"
```

```yaml
# Subscriber example (e.g. notification handler)
events:
  subscribes:
    - topic: user.notify
      handler: notify_user
```

```yaml
# Subscriber example (e.g. agent that processes emails)
events:
  subscribes:
    - topic: email.received
      handler: custom
```

Handler semantics (kernel behaviour):

- **notify_user** — Kernel calls `MessageRouter.notify_user(payload["text"])`. Simple forwarding for notification events.
- **custom** — Extension registers its own handler in code via `context.subscribe_event()` during `initialize()`. The manifest documents the subscription for discoverability; the actual wiring happens in extension code.

Loader wires subscriptions from manifest: for `notify_user` it builds the appropriate callable and calls `event_bus.subscribe(topic, handler, subscriber_id=ext_id)`. For `custom`, no automatic wiring; the extension uses `context.subscribe_event()`.

**Why `invoke_agent` is not a built-in handler**: The original proposal included `handler: invoke_agent` where the kernel would invoke an `AgentProvider` with the event payload as a prompt. This was removed because the kernel cannot generically convert `payload: dict` into a meaningful prompt string — that logic is domain-specific and belongs in the extension. An agent-extension that reacts to events uses `custom` and formats its own prompt:

```python
async def initialize(self, context: ExtensionContext) -> None:
    context.subscribe_event("email.received", self._handle_email)

async def _handle_email(self, event: Event) -> None:
    prompt = f"Analyze this email:\n{json.dumps(event.payload, indent=2)}"
    await self._ctx.invoke_agent(prompt)
```

This keeps the kernel free of topic-specific payload interpretation. If a standardized `invoke_agent` handler is needed later, it can be added with an explicit `prompt_template` field in the manifest.

Manifest model addition (conceptual):

```python
class EventPublishDeclaration(BaseModel):
    topic: str
    description: str = ""

class EventSubscribeDeclaration(BaseModel):
    topic: str
    handler: Literal["notify_user", "custom"] = "custom"

class EventsConfig(BaseModel):
    publishes: list[EventPublishDeclaration] = Field(default_factory=list)
    subscribes: list[EventSubscribeDeclaration] = Field(default_factory=list)

# ExtensionManifest gains:
# events: EventsConfig | None = None
```

`depends_on` remains independent of events (load order only); no mandatory dependency between publisher and subscriber extensions.

### 8. ExtensionContext changes

The Event Bus introduces a **new** API surface on `ExtensionContext`. The existing `subscribe()` / `unsubscribe()` methods on `MessageRouter` are a different mechanism and remain unchanged.

| Method | Backend | Purpose |
|--------|---------|---------|
| `context.subscribe(event, handler)` | `MessageRouter._subscribers` (in-memory) | Middleware hooks: `user_message`, `agent_response`. Synchronous dispatch, no persistence. |
| `context.subscribe_event(topic, handler)` | `EventBus` (durable journal) | Event-driven flows: `email.received`, `tick`, arbitrary topics. Async dispatch via journal. |
| `context.emit(topic, payload)` | `EventBus.publish(...)` | Publish an event to the bus. Fire-and-forget. |

Why separate methods: `MessageRouter.subscribe` dispatches immediately and in-process — it is a middleware hook for the reactive path. `EventBus.subscribe` goes through the durable journal with async dispatch. These are different delivery semantics and should not be conflated behind one method.

```python
class ExtensionContext:

    async def emit(
        self, topic: str, payload: dict,
        correlation_id: str | None = None,
    ) -> None:
        """Publish event to the Event Bus. Fire-and-forget."""
        await self._event_bus.publish(topic, self.extension_id, payload, correlation_id)

    def subscribe_event(
        self, topic: str, handler: Callable[[Event], Awaitable[None]],
    ) -> None:
        """Subscribe to durable events via the Event Bus."""
        self._event_bus.subscribe(topic, handler, self.extension_id)
```

Existing `context.subscribe()` and `context.unsubscribe()` continue to work exactly as before — they still target `MessageRouter` for middleware hooks.

### 9. Lifecycle and concurrency

- **Startup**: Create `EventBus(db_path)`, call `await event_bus.recover()`, then `await event_bus.start()` (starts the dispatch Task). Wire manifest-driven subscriptions during loader initialization (after contexts are created).
- **Shutdown**: `await event_bus.stop()` so in-flight handlers complete.
- One dispatch Task is enough for a single-user, single-process setup; parallelism is across events (and I/O inside handlers). If needed later, the bus could support multiple worker Tasks behind the same API.
- Contention with the reactive path: if a user message and an event-driven agent invocation both hit the orchestrator, the existing `MessageRouter` serialization (e.g. lock around `invoke_agent`) still applies; the Event Bus simply feeds into that path when a handler calls the agent or notifies the user.

### 10. What remains unchanged

- **SchedulerProvider** and the cron loop — Unchanged. Schedulers can keep using `execute() → return {"text": "..."}` and `notify_user`. Migration to `ctx.emit(topic, payload)` is optional and incremental.
- **MessageRouter** — Still used for user messages, `notify_user`, and middleware hooks (`subscribe`/`_emit`). Not replaced by the Event Bus.
- **AgentProvider, ToolProvider, ChannelProvider, etc.** — No protocol changes.
- **depends_on** — Still only for load order; not used for event routing.
- **context.subscribe() / context.unsubscribe()** — Still wired to `MessageRouter` for middleware hooks.

The Event Bus is an **additive** piece of infrastructure; existing behaviour stays as-is.

## Consequences

### Benefits

- **One mechanism, three uses**: extension↔agent event flows, proactive/kernel-driven reaction to events, and observability/audit from the same journal.
- **At-least-once delivery**: Events are stored before dispatch; restarts do not drop pending work; `processing` recovery avoids lost events when the process dies mid-handler.
- **Standalone**: No Redis or other external services; SQLite keeps the app self-contained.
- **Causal tracing**: `correlation_id` links event chains for debugging and future agent tracing (ADR 003 Phase 3).
- **Clear separation of concerns**: `MessageRouter.subscribe` = middleware hooks (in-memory, immediate). `EventBus.subscribe` = durable event flows (journal-backed, async). No semantic ambiguity.

### Trade-offs

- **Eventual delivery**: Events are processed asynchronously by the dispatch loop; no synchronous "emit and wait for handler" in the API. This is intentional (decoupling and persistence).
- **Single process**: The design targets one process; scaling to multiple consumers would require a different transport or partitioning strategy later.
- **No built-in schema for payloads**: Topics and payload shape are convention; per-topic Pydantic schemas can be introduced later (e.g. in manifest or handler registration) without changing the Event Bus API.
- **No auto-retry**: Failed events stay in the journal but are not retried automatically. Retry with backoff can be added later when a concrete use case demands it.

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Journal growth over months of operation | Medium | Periodic cleanup of `done`/`failed` events older than N days (configurable). Can be a background task or a maintenance command. Not in MVP scope but schema supports it (`created_at` index). |
| No retry for transient failures | Low | Acceptable for MVP: schedulers produce events on their own cadence; LLM retries are better handled at the caller level. Schema is forward-compatible with `attempts`/`next_attempt_at` columns. |
| Same-topic ordering in concurrent batches | Low | MVP processes batch events concurrently. If strict per-topic ordering is needed, add per-topic serial dispatch as an enhancement. Most topics are independent. |
| Topic proliferation without discoverability | Low | Manifest `events.publishes` section documents available topics. Wildcard/pattern subscriptions (`email.*`) can be added later if topic count grows. |

### Implementation location

Implementation lives under **`core/events/`** (e.g. `models.py`, `bus.py`, storage/journal layer). Loader wiring and context changes are in existing files (`loader.py`, `context.py`, `manifest.py`).

## Relation to other ADRs

- **ADR 002** — Introduced direct callbacks and in-memory pub/sub in the router. This ADR adds a **persistent** event bus alongside that; it does not remove direct callbacks or the router. The two subscribe mechanisms (`MessageRouter` for middleware hooks, `EventBus` for durable events) coexist with clear separation.
- **ADR 003** — Phase 3 mentions agent event tracing (`agent_invoked`, `agent_result`, etc.) and correlation via `correlation_id`. The Event Bus journal with its `correlation_id` column is the natural backbone for such tracing without a second mechanism.

## Alternatives considered

- **In-memory only bus** — Simpler but no durability or audit; rejected for observability and restart safety.
- **Redis (or similar)** — Better for multi-process/multi-node; adds a dependency and operational cost. Rejected for current standalone scope; the bus API allows swapping the backend later.
- **Synchronous publish that calls handlers immediately** — Would tie publisher to handler performance and failure; rejected in favour of journal-then-dispatch.
- **`invoke_agent` as a built-in manifest handler** — Rejected because the kernel cannot generically convert `payload: dict` into a meaningful prompt. Domain-specific prompt formatting belongs in the extension via `custom` handler.
- **Topic wildcard/pattern matching** — Useful but adds complexity to the dispatch lookup. Deferred; can be added to the subscribe API later without breaking existing exact-match subscriptions.

## References

- ADR 002: Nano-Kernel + Extensions (direct callbacks, context.subscribe, MessageRouter)
- ADR 003: Agent-as-Extension (agent tools, Phase 3 observability, `correlation_id`)
