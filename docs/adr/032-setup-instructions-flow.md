# ADR 032: Wire setup_instructions for Extension Setup Flow

## Status

Accepted. Implemented

## Context

The `setup_instructions` field exists in `ExtensionManifest` (ADR 002, ADR 003) and is filled in by some extensions (`mail`, `mcp`), but is never read by any code. ADR 002 describes the intended flow: when an extension implementing `SetupProvider` is not yet configured, `setup_instructions` should be visible to the agent so it can guide the user through configuration.

The `SetupProvider` protocol and its implementations (`telegram_channel`, `web_search`) exist but are never wired by the Loader. There is no `configure_extension` tool; extensions mention it in error messages, but it does not exist. The agent has no structured way to apply configuration or see setup instructions for unconfigured extensions.

## Decision

### 1. Loader detects SetupProvider and tracks configured state

- In `detect_and_wire_all()`, after existing protocol detection, the Loader iterates over extensions and detects `SetupProvider` via `isinstance(ext, SetupProvider)`.
- For each SetupProvider, call `await ext.on_setup_complete()`. If it returns `(False, ...)`, the extension is "unconfigured". Store result in `_setup_providers: dict[str, bool]` (ext_id -> is_configured).
- No new `ExtensionState` value; configuration state is derived from `on_setup_complete()`.

### 2. Capabilities summary includes unconfigured extensions

- Add `_collect_setup_sections()` method that iterates over `_setup_providers` where `is_configured=False`, reads `manifest.setup_instructions`, and builds an "Extensions needing setup" section.
- In `get_capabilities_summary()`, include this section when non-empty.
- The orchestrator receives this context so it knows which extensions need setup and how to guide the user.

### 3. New core tool: configure_extension

- Add `core/tools/configure_extension.py` with `make_configure_extension_tool(extensions, state)` that captures the extensions dict and state.
- The tool accepts `extension_id`, `param_name`, `value`. It:
  1. Looks up extension by ID
  2. Checks `isinstance(ext, SetupProvider)`
  3. Calls `await ext.apply_config(param_name, value)`
  4. Calls `await ext.on_setup_complete()` to verify
  5. Returns structured `ConfigureExtensionResult` (success, message, error)
- Returns structured Pydantic result per agent_tools skill.

### 4. Runner wires the tool

- Add `make_configure_extension_tool` to the channel_tools list in `core/runner.py`. The tool receives the Loader's extensions and state via a new accessor `get_setup_providers()` (or equivalent: the Loader exposes extensions dict for the tool).

### 5. Extension manifests: setup_instructions

- Extensions that implement SetupProvider should define `setup_instructions` in manifest.yaml with human-readable instructions for the agent.
- Example: `telegram_channel` describes using `request_secure_input` for the Bot API token, then `request_restart` to apply.

## Consequences

### Positive

- The agent can see which extensions need setup and how to configure them.
- The agent can use `configure_extension` to apply configuration without manual user intervention.
- Setup flow is consistent across extensions that implement SetupProvider.
- Reuses existing `SetupProvider` protocol; no changes to extension implementations.

### Negative

- `on_setup_complete()` is called during startup for each SetupProvider; extensions that perform async validation (e.g. Telegram API call) may add latency. This is acceptable for extension setup detection.
- The configured state is not refreshed after `configure_extension` until restart; the agent may need to call `request_restart` (as in telegram_channel flow) to apply changes.

### Trade-offs

- We chose to inject setup instructions via capabilities summary rather than a dedicated ContextProvider; simpler and aligns with existing tool/agent info flow.
- We chose not to add a "refresh setup state" mechanism; the agent can call `request_restart` when needed.
