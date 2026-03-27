# ADR 037: Extension Diagnostics and Admission Hardening

## Status

Implemented

## Context

The extension loader already imports programmatic extensions as packages via
`importlib.import_module("sandbox.extensions.<id>.<module>")` per ADR 034.
That fixed the original relative-import fragility caused by file-based loading.

However, the runtime still had a major observability gap:

- extension failures during `load_all()`, `initialize_all()`, `start_all()`, or
  `health_check()` were mostly visible only through logs
- `ExtensionState` collapsed all failures into `ERROR` with no phase or reason
- invalid `ConfigModel` on one extension aborted the entire initialization path
- failed extensions silently disappeared from capabilities instead of becoming
  diagnosable by the agent

This was especially harmful for self-extension workflows, where the agent must
be able to inspect why a newly created extension did not become available.

## Decision

1. Keep `ExtensionState` unchanged:
   - `INACTIVE`
   - `ACTIVE`
   - `ERROR`

2. Add a separate Loader-managed diagnostics registry:
   - bounded in-memory history per extension
   - structured fields for phase, reason, message, traceback, dependency chain

3. Record diagnostics for failures in:
   - `load_all()`
   - config validation before initialize
   - `initialize_all()`
   - `start_all()`
   - periodic `health_check()`

4. Change config validation semantics:
   - invalid `ConfigModel` marks only that extension `ERROR`
   - bootstrap continues for other extensions

5. Expose diagnostics through:
   - Loader report APIs
   - `extensions_doctor` core tool
   - Event Bus topic `system.extension.error`

6. Do not include failed extensions in the normal capabilities summary by
   default; diagnostics are available through explicit inspection surfaces.

## Consequences

Positive:

- self-extension workflows can diagnose missing tools/extensions explicitly
- one broken extension no longer blocks the rest of the runtime
- dependency cascades become explainable without complicating `ExtensionState`
- monitoring and future self-healing extensions can subscribe to
  `system.extension.error`

Negative:

- Loader now owns additional in-memory state and reporting APIs
- diagnostics history is not persisted across restarts in this version
- re-admission / hot-reload remains a separate follow-up concern
