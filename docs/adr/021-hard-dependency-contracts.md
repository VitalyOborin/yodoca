# ADR 021: Hard Dependency Contracts

## Status

Accepted. Implemented

## Context

Extensions declare dependencies via `depends_on` in their manifest. The Loader uses this for topological load order and for authorizing `context.get_extension(ext_id)` calls. However, when a dependency failed to load (e.g., import error, missing file), the dependent extension still proceeded through the full lifecycle (load, initialize, start). `get_extension()` returned `None` instead of failing, leading to:

- "Half-alive" extensions that start but cannot function correctly
- Late runtime failures when the extension first uses the missing dependency
- Difficult-to-diagnose errors (e.g., `AttributeError: 'NoneType' has no attribute 'get'`)

The contract implied by `depends_on` was not enforced: dependents assumed their dependencies were available, but the Loader did not guarantee that.

## Decision

1. **`depends_on` is a hard contract.** If any declared dependency is unavailable (failed to load, initialize, or start), the dependent extension is cascaded to ERROR at every lifecycle stage. It is never initialized or started.

2. **Cascade at each stage:**
   - **load_all:** Track `failed_ids`. Before loading each extension, skip it and mark ERROR if any `depends_on` entry is in `failed_ids`. On load exception, add to `failed_ids`.
   - **initialize_all:** Before `initialize(ctx)`, skip and mark ERROR if any `depends_on` is in ERROR state.
   - **start_all:** Before `start()`, skip and mark ERROR if any `depends_on` is in ERROR state.

3. **`get_extension()` is fail-fast.** When a caller requests a dependency that is in `depends_on` but:
   - is not in `_extensions` (never loaded), or
   - is in `_state` as ERROR,
   then `get_extension()` raises `RuntimeError` instead of returning `None`.

4. **Soft/optional dependencies.** Extensions that need optional access to another extension must not declare it in `depends_on`. They must use an alternative mechanism (e.g., the capability layer via `model_router.get_capability()`) and handle absence gracefully.

## Consequences

- **Fail-fast:** Dependency failures surface immediately at startup, not at first use.
- **Predictable behavior:** Extensions with `depends_on` can assume dependencies are available when they reach `initialize()` and `start()`.
- **Migration:** The `memory` extension previously used `depends_on: [embedding]` as a soft dependency (degraded to keyword search when embedding was unavailable). It now uses `model_router.get_capability(EmbeddingCapability)` directly and removes `embedding` from `depends_on`.
- **Documentation:** `docs/extensions.md` and the extensions SKILL are updated to describe the hard contract and fail-fast behavior.
