# ADR 022: Move Prompts Directory to Sandbox

## Status

Accepted. Implemented

## Context

System agent prompts (Orchestrator, default) currently reside in the root `prompts/` directory. The project follows an "all-is-extension" principle where user-facing functionality lives under `sandbox/`. Moving prompts to `sandbox/prompts/` aligns system prompts with this structure and keeps all runtime-configurable content under `sandbox/`.

## Decision

1. **Relocate directory:** Move `prompts/` to `sandbox/prompts/`.
2. **Update path references:** All config and code that reference `prompts/` (e.g. `prompts/orchestrator.jinja2`) will use `sandbox/prompts/` (e.g. `sandbox/prompts/orchestrator.jinja2`).
3. **Resolution logic unchanged:** The Orchestrator and config resolution continue to resolve paths relative to project root (`_PROJECT_ROOT / spec`). Only the default spec values change.

## Consequences

### Positive

- Consistent layout: prompts live alongside extensions and data under `sandbox/`.
- Easier to reason about what is "sandbox" vs "core" vs "config".

### Migration

- Existing `config/settings.yaml` with `instructions: prompts/orchestrator.jinja2` will break until users update to `sandbox/prompts/orchestrator.jinja2` or re-run onboarding.
- Onboarding and default config will write the new path; no manual migration for new installs.
