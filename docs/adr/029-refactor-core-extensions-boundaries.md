# ADR 029: Refactor `core.extensions` Boundaries

## Status

Implemented

## Context

`core/extensions/` accumulated several distinct responsibilities in one flat package:

- extension lifecycle orchestration
- message routing and agent invocation
- session/project persistence
- built-in context providers and event wiring

The heaviest coupling points were:

- `MessageRouter` acting as both routing coordinator and session/project CRUD facade
- `ExtensionContext` proxying persistence through `MessageRouter`
- session and project schema ownership living inside `SessionRepository`
- duplicated manifest iteration logic in loader-related code

This made testing harder, increased pass-through code, and blurred domain boundaries.

## Decision

Refactor internals around explicit service boundaries while preserving the public `core.extensions` entrypoints:

1. Introduce typed persistence models:
   - `SessionInfo`
   - `ProjectInfo`

2. Move shared SQLite DDL into a dedicated schema module used by both repositories.

3. Remove session/project CRUD responsibilities from `MessageRouter`.

4. Inject `SessionManager` and `ProjectService` directly into `ExtensionContext`.

5. Update the built-in project context provider to depend on persistence services instead of `MessageRouter`.

6. Deduplicate active-manifest iteration into a shared utility and remove dead loader code.

7. Replace the untyped `UNSET = object()` sentinel with a typed enum-based sentinel.

The physical package layout remains flat for now to avoid broad import churn during the same change.

## Consequences

Positive:

- `MessageRouter` is focused on channel registration, event emission, and agent-response flow
- persistence concerns are explicit and testable without routing indirection
- schema ownership is centralized
- types at repository/service boundaries are stronger
- loader/event-wiring internals share common iteration logic

Negative:

- internal APIs changed for tests and callers using non-public implementation details
- `core/extensions/` is still a flat package; a later move to subpackages remains possible
