# ADR 024: Unified Inbox Extension

## Status

Approved.

## Context

The platform needs a single internal storage for incoming data from external systems.
Future source extensions (for example `mail`, `github`, `gitlab`, `jira`, `confluence`)
must ingest from external providers and persist normalized records into one shared place:
`inbox`.

The initial use case is `mail`:

1. `mail` periodically checks external mailboxes.
2. New messages are saved via `inbox` API.
3. `mail` does not own separate durable storage for mail objects.

The same ingestion pattern must be reusable by other source extensions.

Constraints and principles:

- local single-user runtime (no enterprise-scale requirements);
- no over-engineering;
- preserve extension architecture and contracts;
- keep `core/` independent from concrete extensions;
- downstream processing must stay decoupled from ingestion.

## Decision

### 1) Create extension `inbox` as internal ingestion storage

Add a new extension at `sandbox/extensions/inbox/` that is responsible for:

- storing incoming source records in unified format;
- owning source cursors (incremental sync checkpoints);
- emitting Event Bus notifications after successful persistence;
- exposing read/query tools to the Orchestrator (ToolProvider).

`inbox` is a storage and ingestion-boundary extension, not a notification center and not a
business-processing engine.

Protocols implemented: `ToolProvider` (agent read/search tools).

### 2) Interaction contract: source extension -> inbox

Source extensions depend on `inbox` and use its service API via
`depends_on: [inbox]` + `context.get_extension("inbox")`, following the standard
extension dependency contract (see `docs/extensions.md` §Dependency Order).

#### Write API (for source extensions)

- `upsert_item(input: InboxItemInput) -> InboxWriteResult`

#### Read API (for source extensions and event consumers)

- `get_item(inbox_id: int) -> InboxItem | None`
- `list_items(source_type?, entity_type?, status?, limit?, offset?) -> list[InboxItem]`

Consumers that receive an `inbox.item.ingested` event use `get_item(inbox_id)` to fetch
the full record including payload.

#### Cursor API

- `get_cursor(source_type, source_account, stream) -> str | None`
- `set_cursor(source_type, source_account, stream, value: str) -> None`

Cursor scope is strictly tied to source identity:

- `source_type` (e.g. `mail`, `gitlab`);
- `source_account` (configured account/connection);
- `stream` (mailbox/folder/webhook stream/etc.).

**Cursor values are opaque strings.** Inbox stores and returns them without
interpretation. The producer defines the semantics: UID, timestamp, offset, page token,
etc. This avoids coupling Inbox to any specific provider's pagination model.

Typical sync flow (using `mail` as example):

1. `mail` calls `get_cursor(...)` to get the last sync position;
2. fetches only newer messages from provider starting from cursor;
3. calls `upsert_item(...)` for each message;
4. calls `set_cursor(...)` only after all items are persisted successfully.

Cursor is updated last to guarantee that a crash mid-sync resumes from the correct
position (at-most-once cursor advance).

### 3) Agent tools (ToolProvider)

Inbox implements `ToolProvider` to expose read/search tools to the Orchestrator and
delegated agents. Example tools:

- `inbox_list` — list/filter items by source, entity type, status;
- `inbox_read` — get a single item by `inbox_id`.

Phase 2 (not part of MVP):

- `inbox_search` — text search over payload (FTS5 or similar).

All tools return structured output (Pydantic models), per project conventions.
Write operations are not exposed as agent tools — only source extensions write via
the service API.

### 4) Unified envelope with source payload

Inbox stores each item as a normalized envelope plus source-specific payload JSON.

Envelope fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Auto-increment primary key (= `inbox_id` in events) |
| `source_type` | str | e.g. `mail`, `gitlab` |
| `source_account` | str | Account/connection identifier within source type |
| `entity_type` | str | e.g. `email.message`, `gitlab.merge_request` |
| `external_id` | str | Stable source object identifier |
| `title` | str | Human-readable summary for quick triage without payload parsing |
| `occurred_at` | float | Timestamp from source (unix epoch) |
| `ingested_at` | float | Local ingest timestamp |
| `status` | str | `active` or `deleted` (see §6) |
| `is_current` | bool | True for the latest version of a mutable entity |
| `payload` | str | JSON with source-specific fields |
| `payload_hash` | str | SHA-256 hex digest of `payload`; used for duplicate suppression |

Idempotency constraint (unique partial index):
`(source_type, source_account, entity_type, external_id)` where `is_current = true`.

For immutable entities (like email) this prevents duplicate ingestion.
For mutable entities this ensures only one current version exists at a time.

### 5) Mutable entities: history + current projection

Inbox must support both immutable and mutable source entities.

- **Immutable** (`mail`): each `external_id` is written once (or idempotently upserted).
- **Mutable** (`gitlab` merge request): source object changes over time.

For mutable entities Inbox uses simple versioning:

1. mark previous current version as `is_current = false`;
2. insert new version with `is_current = true` and updated payload;
3. keep source `updated_at`/`version` in payload for traceability.

Both steps are performed within a single SQLite transaction to guarantee atomicity.

This keeps history and one current view without event-sourcing complexity.

### 6) Soft-delete

Source extensions may signal that an object was removed at the source
(e.g. email moved to trash, MR closed and deleted).

In that case the producer calls `upsert_item(...)` with `status = "deleted"`. Inbox
marks the current version as deleted and emits an event with `change_type = "deleted"`.

Deleted items are retained in storage for auditability but excluded from default
`list_items` results (filter `status = "active"` by default).

### 7) Event Bus responsibility

Inbox does not decide downstream business logic.
After successful insert or update it emits an Event Bus event:

- topic: `inbox.item.ingested`
- payload:
  - `inbox_id` (int)
  - `source_type` (str)
  - `source_account` (str)
  - `entity_type` (str)
  - `external_id` (str)
  - `title` (str)
  - `change_type` (`created` | `updated` | `deleted`)
  - `occurred_at` (float)
  - `ingested_at` (float)

**Duplicates are not emitted.** If `upsert_item` receives a record identical to the
current version (same `external_id`, matching `payload_hash`), no new row is created
and no event is emitted. This prevents downstream noise from at-least-once delivery.

Consumers (e.g. triage agent) subscribe, receive lightweight event metadata, and call
`get_item(inbox_id)` if they need the full payload.

### 8) Write-emit consistency

Inbox storage (SQLite in `context.data_dir`) and Event Bus journal (separate SQLite DB)
are two independent databases. A crash between a successful Inbox DB commit and the
subsequent `context.emit(...)` call would leave a persisted item with no corresponding
event — consumers would never learn about it.

For MVP this window is accepted as a known limitation. The risk is low in a local
single-user runtime where crashes are infrequent and the window is measured in
milliseconds.

Mitigation path (Phase 2): add a lightweight reconciliation mechanism — a periodic
check that compares the latest Inbox `ingested_at` watermark against the last emitted
`inbox.item.ingested` event and re-emits for any gap. This can be implemented as a
`SchedulerProvider` task within the `inbox` extension itself, without core changes.

### 9) Polling and webhooks support

Inbox contract is transport-agnostic:

- producer may ingest by periodic polling (incremental cursor sync);
- producer may ingest by webhook delivery (at-least-once, duplicates possible).

Inbox write path is idempotent (upsert + duplicate suppression) to tolerate
duplicate deliveries from either transport.

### 10) Manifest sketch

```yaml
id: inbox
name: Inbox
version: "1.0.0"
entrypoint: main:InboxExtension

description: |
  Unified storage for incoming data from external systems.
  Source extensions (mail, github, gitlab, etc.) persist records via Inbox API.
  Tools: inbox_list (list/filter items), inbox_read (read single item).

depends_on: []
config: {}
enabled: true

events:
  publishes:
    - topic: inbox.item.ingested
      description: Emitted after successful item create, update, or soft-delete
```

### 11) Core impact

No mandatory core architecture changes are required for initial implementation.
Current mechanisms are sufficient:

- `depends_on` for extension dependencies;
- `context.get_extension(...)` for extension-to-extension service calls;
- `context.emit(...)` for Event Bus publication;
- `context.data_dir` for SQLite storage path.

Optional future improvement (not in this ADR scope):

- define typed shared protocol contracts for extension services to reduce coupling to
  concrete extension classes (e.g. an `InboxService` Protocol that both the extension
  class and consumers can type against).

## Consequences

### Positive

- single canonical ingestion storage for all external sources;
- no duplicated cursor/dedup logic in source extensions;
- clean layering: source extensions ingest, Inbox persists and emits, consumers process;
- agent can query inbox via tools without knowing source specifics;
- supports both immutable and mutable entities with minimal complexity;
- easier onboarding of new integrations using one contract.

### Trade-offs

- source extensions depend on Inbox availability and API contract;
- payload remains partially source-specific (limited deep cross-source normalization);
- version history for mutable entities increases local storage volume
  (acceptable for local single-user setup).

### Risks and mitigations

- **Duplicate source deliveries** (polling overlap, webhook retries):
  idempotent upsert + `payload_hash` comparison, no event emitted for duplicates.
- **Cursor drift on partial failure**:
  cursor is updated only after all items are persisted successfully.
- **Inconsistent event shape across producers**:
  Inbox is the sole emitter of `inbox.item.ingested`; envelope is fixed.
- **Write-emit gap** (crash between DB commit and `context.emit`):
  accepted for MVP; reconciliation job planned for Phase 2 (see §8).

## Implementation scope

1. Create `sandbox/extensions/inbox/` with manifest, SQLite repository, cursor table.
2. Define typed input/output Pydantic models for all API methods (structured I/O only).
3. Implement `ToolProvider` with `inbox_list` and `inbox_read` tools.
4. Emit `inbox.item.ingested` after successful write; suppress duplicates.
5. First producer integration: `mail` extension using Inbox service API only.
6. Update docs after implementation:
   - `docs/extensions.md` (new extension)
   - `docs/event_bus.md` (new `inbox.item.ingested` topic)
   - `docs/README.md` (ADR table)
