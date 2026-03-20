# ADR 034: Package Imports for Extensions (Remove `sys.path` Hacks)

## Status

Accepted. Implemented.

## Context

Extensions were loaded from files via synthetic module names (`ext_<id>_<module>`).  
This led to inconsistent import patterns inside extensions:

- local bare imports (for example `from chains import ...`)
- explicit `sys.path.insert(...)` hacks in several high-churn extensions
- test workarounds using file-based module loading

At the same time, other parts of the codebase already used package imports such as
`sandbox.extensions.inbox.*` and `sandbox.extensions.web_channel.*`.

This inconsistency increased maintenance cost and made imports fragile.

## Decision

1. Loader imports extension entrypoints as packages:
   - `sandbox.extensions.<extension_id>.<module>`
2. Manifest format stays unchanged:
   - `entrypoint: main:ClassName`
3. Extension directories are regular Python packages:
   - add `__init__.py` to each `sandbox/extensions/<id>/`
4. `sys.path.insert(...)` in extension runtime code is disallowed.
5. `tests/conftest.py` remains the only approved `sys.path` exception to ensure
   project-root importability in tests.

## Consequences

### Positive

- One import model across loader, extensions, and tests.
- No synthetic module names in `sys.modules`.
- Relative/absolute package imports behave predictably.

### Trade-offs

- Import refactor required in affected extensions and tests.
- Requires running from project root (or equivalent PYTHONPATH setup) so
  `sandbox.extensions.*` resolves.

