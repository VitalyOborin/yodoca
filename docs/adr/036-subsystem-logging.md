# ADR 036: Subsystem Logging Factory

## Status

Accepted. Implemented.

## Context

The process used a single global log level on the root logger. Debugging one extension or core area required setting everything to DEBUG, flooding operators and log files. There was no structured metadata channel for observability pipelines, no independent console vs file verbosity, and no extension point for forwarding logs (e.g. OpenTelemetry) without touching core.

The design is informed by the OpenClaw project’s `src/logging` subsystem pattern: `createSubsystemLogger`, independent console/file levels, JSON output, child loggers, and pluggable transports—adapted to Python’s stdlib `logging`.

## Decision

1. **Settings** — Extend `LoggingSettings` in [`core/settings_models.py`](../../core/settings_models.py) with:
   - `console_level` (optional; defaults to `level`)
   - `console_style` / `file_style`: `text` | `json`
   - `subsystems`: map of logger name prefix → level string (longest prefix wins)
   - `console_subsystems`: if non-empty, only those prefixes are emitted to the console handler

2. **Implementation** — Centralize in [`core/logging_config.py`](../../core/logging_config.py):
   - `SubsystemFilter` on file and console handlers: enforces per-prefix minimum level and optional console allowlist; handlers stay at `DEBUG`, root level remains the global default so third-party loggers do not emit below global unless configured.
   - `JsonFormatter` for line-delimited JSON (`timestamp`, `level`, `logger`, `message`, optional `meta`, optional `exception`).
   - `SubsystemLogger`: wraps `logging.Logger` with `.child(name)`, `.is_enabled(level, target)` (`any` | `console` | `file`), and `meta=` on `debug`/`info`/`warning`/`error`/`exception` (stored as `extra["_meta"]` for formatters). Other methods delegate via `__getattr__`.
   - `create_subsystem_logger(subsystem: str)` — public factory.
   - `register_log_transport(fn) -> unregister` — dispatches every `LogRecord` to registered callbacks via a dedicated root handler (for OTLP or custom sinks).

3. **Extensions** — [`ExtensionContext`](../../core/extensions/context.py) receives `SubsystemLogger` from [`context_builder`](../../core/extensions/loader/context_builder.py) (`ext.<id>`).

4. **Naming** — Extensions: `ext.<extension_id>`; children: `ext.<id>.<child>`. Core modules may keep `logging.getLogger(__name__)`; `subsystems` keys match those dotted names by prefix.

5. **Out of scope (for now)** — OTLP exporter implementation (transport hook is the prerequisite); colored “pretty” console style; migrating every module to `create_subsystem_logger` (optional).

## Consequences

### Positive

- Targeted DEBUG for one subsystem without global noise.
- JSON file/console output ready for aggregation and future OTLP.
- Structured `meta` without adding structlog.
- Transports decouple observability exporters from `setup_logging` internals.

### Negative / trade-offs

- Root always has an extra transport dispatch handler (even with zero transports).
- `is_enabled` depends on the last `setup_logging` call (process-global resolution snapshot).
- Module-level `getLogger(__name__)` names differ from `ext.<id>` unless teams standardize on one hierarchy for overrides.
