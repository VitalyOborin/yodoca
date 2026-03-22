# ADR 035: Pydantic Settings Models for Validated Configuration

## Status

Accepted. Implemented.

## Context

Application settings were loaded from `config/settings.yaml` into a plain `dict[str, Any]`, deep-merged over in-code defaults. Invalid keys (typos), wrong types, and malformed nested structures were silently ignored or caused subtle runtime failures. Extension configuration was similarly untyped. This is a production risk for a self-hosted agent runtime.

Pydantic is already a project dependency (`pydantic>=2.0.0`).

## Decision

1. **Core settings** — Introduce `AppSettings` and nested Pydantic `BaseModel` types in `core/settings_models.py` for sections: `supervisor`, `agents`, `providers`, `event_bus`, `logging`, `thread`, `extensions` (as `dict[str, dict[str, Any]]` until per-extension validation), and `models` (model catalog overrides).

2. **Loading** — `load_settings()` merges YAML over `AppSettings()` defaults, then `AppSettings.model_validate(merged_dict)`. On `ValidationError`, print path-style diagnostics and exit the process (fail-fast).

3. **Consumers** — Core and supervisor use typed `AppSettings` (attribute access). `get_setting()` remains for backward compatibility but accepts `AppSettings | dict[str, Any]`.

4. **Extension config** — Extensions may declare an optional `ConfigModel` class attribute (`type[BaseModel]`). After merging manifest `config` with `settings.extensions.<id>`, the Loader validates the merged dict against `ConfigModel` before `initialize()`. If any enabled extension fails validation, startup fails (fail-fast).

5. **Manifest** — No new manifest field for schemas; schema lives next to the extension class.

## Consequences

### Positive

- Structural misconfiguration is caught at startup with clear diagnostics.
- IDE-friendly types for core settings.
- Extensions can opt in to strict config without changing `get_config()` API.

### Negative / trade-offs

- Adding a new top-level settings key requires updating `AppSettings` (explicit schema evolution).
- Extension `ConfigModel` is optional; extensions without it are not structurally validated beyond merge.
