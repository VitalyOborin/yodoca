# ADR 037: Trace hook registration on ExtensionContext

## Status

Accepted. Implemented

## Context

The tracing extension registered execution trace hooks by reaching into core internals: `context._router._invoker.register_trace_hook(...)`. That violates the core–extension boundary: extensions must depend on `ExtensionContext` only, not on `MessageRouter` or `AgentInvoker` layout.

## Decision

Expose a single method on `ExtensionContext`:

- `register_trace_hook(hook: Any) -> None` — delegates to `self._router._invoker.register_trace_hook(hook)` when the invoker supports it (duck-typed `hasattr` check).

Extensions (e.g. `tracing`) call `context.register_trace_hook(self)` during `initialize()`. Coupling to invoker wiring stays inside `ExtensionContext`, consistent with other router delegations (`invoke_agent`, `enrich_prompt`, etc.).

## Consequences

**Positive:**

- Extensions no longer import or traverse private router/invoker attributes.
- Future refactors of `MessageRouter` / `AgentInvoker` can keep `register_trace_hook` stable on `ExtensionContext`.

**Negative:**

- `ExtensionContext` gains a narrow, tracing-specific API surface; acceptable as an explicit extension capability.

**Migration:**

- Replace direct `_router._invoker` access with `register_trace_hook`; update tests to mock `context.register_trace_hook`.
