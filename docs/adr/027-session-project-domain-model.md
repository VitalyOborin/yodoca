# ADR 027: Session and Project Domain Model in `session.db`

## Status

Implemented

## Context

Session state was previously split across:

- runtime-only `SessionManager` fields
- OpenAI Agents SDK `agent_sessions` / `agent_messages`
- `yodoca_session_meta`
- memory and event-journal payloads that only carried `session_id`

This made sessions a technical identifier instead of a first-class domain entity. The web API also exposed session state through conversation-oriented endpoints backed by the in-memory pool instead of durable metadata.

## Decision

Introduce two persistent domain entities in `session.db`:

- `sessions` as the canonical metadata index for chat threads
- `projects` and `project_files` as an aggregate root over sessions

Key decisions:

- `session.db` remains the single metadata store colocated with `agent_messages`
- `SessionRepository` owns schema bootstrap, migration, and CRUD for sessions
- `ProjectRepository` and `ProjectService` own project persistence and session binding
- `SessionManager` keeps only runtime `SQLiteSession` lifecycle plus repository synchronization
- `TurnContext` stays unchanged and continues to carry only `session_id`
- project instructions are injected through a built-in core `ContextProvider` at priority `10`, between channel context and memory
- web API uses `/api/sessions` and `/api/projects`; the old conversation terminology is removed instead of preserved

## Consequences

Positive:

- session discovery survives process restarts
- session history and metadata now share one durable SQLite source
- project-level instructions can be injected without changing the agent contract
- web, CLI, and Telegram can share the same repositories and services

Negative:

- this is a clean break for the web API and any conversation-oriented clients
- startup migration must backfill historical sessions and normalize timestamps
- `channel_id` for old sessions is only best-effort and defaults to `unknown`
