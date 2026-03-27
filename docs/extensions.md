# Extensions System

This document describes the extension architecture in the assistant4 system: how extensions work, their types, interactions with the Event Bus, core kernel, and Orchestrator, plus manifest schema and practical guidance for developers.

---

## Overview

Extensions are **pluggable modules** that extend the system with tools, channels, agents, schedulers, and services. They live in `sandbox/extensions/<id>/` and are discovered, loaded, and wired by the **Loader** at startup. Capabilities are detected via **`@runtime_checkable` Protocol classes** (`core/extensions/contract.py`), not via manifest fields.

**Key principle:** Extensions interact with the system **only** through `ExtensionContext` and protocol contracts — no direct imports of loader, routing, or persistence internals. Automated boundary checks enforce this at CI time; see [boundary-checks.md](boundary-checks.md).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              Runner (bootstrap)                                         │
│  discover → load_all → initialize_all → detect_and_wire_all → wire_event_subscriptions  │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                       │
          ┌────────────────────────────┼─────────────────────────────┐
          ▼                            ▼                             ▼
┌──────────────────┐         ┌──────────────────┐         ┌─────────────────────┐
│     Loader       │         │  MessageRouter   │         │    EventBus         │
│  - manifests     │         │  - channels      │         │  - durable pub/sub  │
│  - extensions    │         │  - threads      │         │  - system topics    │
│  - lifecycle     │         │  - invoke_agent  │         │  - recovery         │
│  - protocol      │         │  - notify_user   │         │                     │
│    detection     │         │  - delivery      │         │                     │
└────────┬─────────┘         └─────────┬────────┘         └──────────┬──────────┘
         │                             │                             │
         ▼                             ▼                             ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                         Extensions (sandbox/extensions/<id>/)                          │
│                    ToolProvider │ ChannelProvider │ AgentProvider                      │
│                SchedulerProvider │ ServiceProvider │ ContextProvider                   │
└────────────────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│   Orchestrator   │  ← Agent with extension tools + delegation tools (list_agents, delegate_task)
│  (core/agents)   │
└──────────────────┘
```

---

## Bootstrap Flow (Runner)

The startup sequence in `core/runner.py`:

1. **discover** — Scan `sandbox/extensions/` for `manifest.yaml`; load manifests, filter `enabled: true`
2. **load_all** — Topological sort by `depends_on`; dynamic import or `DeclarativeAgentAdapter`; instantiate
3. **initialize_all** — Create `ExtensionContext` per extension; call `initialize(ctx)`
4. **_update_setup_providers_state** — For each SetupProvider, call `on_setup_complete()`; store configured vs unconfigured
5. **detect_and_wire_all** — `isinstance(ext, Protocol)`; wire ToolProvider, ChannelProvider, AgentProvider, SchedulerProvider
6. **wire_event_subscriptions** — Wire manifest-driven `notify_user` / `invoke_agent`; kernel `user.message` handler
7. **create_orchestrator_agent** — Merge core tools + `get_all_tools()` + delegation tools + capabilities summary
8. **configure_thread** — `router.configure_thread()` creates persistent thread/project services and the default runtime thread
9. **wire_context_providers** — Collect `ContextProvider` extensions plus built-ins, chain into router middleware
10. **start** — EventBus, then `loader.start_all()` (extensions' `start()`, ServiceProvider tasks, cron + health loops)

Shutdown: `event_bus.stop()` → `loader.shutdown()` (reverse dependency order: cancel service/cron/health tasks → `stop()` → `destroy()`).

---

## Extension Types (Protocols)

Capabilities are determined by **runtime protocol checks** (`isinstance`). One extension can implement multiple protocols.

### Base: `Extension`

All extensions must implement the lifecycle:

| Method | Purpose |
|--------|---------|
| `initialize(context)` | Called once. Setup, subscriptions, dependency init. |
| `start()` | Start active work: polling loops, servers, background tasks. |
| `stop()` | Graceful shutdown. Cancel tasks, close connections. |
| `destroy()` | Release resources. Called after `stop()`. |
| `health_check()` | Return `True` if operating normally. Called every 30s by Loader. |

### `ToolProvider`

Provides callable tools for the Orchestrator agent.

```python
def get_tools(self) -> list[Any]:
    """List of @function_tool objects."""
```

Tools are merged into the Orchestrator via `loader.get_all_tools()`.

### `ChannelProvider`

User communication channel. Receives agent responses and sends them to the user.

```python
async def send_to_user(self, user_id: str, message: str) -> None:
    """Reactive: reply to a specific user who sent a message."""

async def send_message(self, message: str) -> None:
    """Proactive: deliver to the channel's default recipient.
    All addressing (user_id, chat_id, etc.) is internal to the channel."""
```

Loader registers channels in `MessageRouter`. `notify_user(text, channel_id)` routes to the specified channel or the default one. For proactive delivery, the kernel calls `send_message(text)` — the channel handles addressing internally.

### `StreamingChannelProvider` (optional, for channels)

Channels can implement this protocol **in addition to** `ChannelProvider` to receive incremental response delivery. The kernel uses `Runner.run_streamed()` and calls the channel's lifecycle methods instead of `send_to_user()` once at the end.

```python
async def on_stream_start(self, user_id: str) -> None: ...
async def on_stream_chunk(self, user_id: str, chunk: str) -> None: ...
async def on_stream_status(self, user_id: str, status: str) -> None: ...
async def on_stream_end(self, user_id: str, full_text: str) -> None: ...
```

See [ADR 010](adr/010-streaming.md) and [channels.md](channels.md#streaming).

### `AgentProvider`

Specialized AI agent. Can be exposed as a **tool** (Orchestrator calls it) or **handoff** (future: direct routing).

```python
def get_agent_descriptor(self) -> AgentDescriptor:
    """Metadata for LLM routing: name, description, integration_mode."""

async def invoke(self, task: str, context: AgentInvocationContext | None = None) -> AgentResponse:
    """Execute task; return structured result."""
```

- **integration_mode: "tool"** — Registered in `AgentRegistry`; Orchestrator invokes via `delegate_task` tool
- **integration_mode: "handoff"** — Reserved for future direct routing

### `SchedulerProvider`

Periodic tasks by schedules from manifest.yaml. Loader reads the `schedules` section and calls `execute_task(task_name)` per cron trigger. Cron loop runs every 60 seconds.

```python
async def execute_task(self, task_name: str) -> dict[str, Any] | None:
    """Execute task by name from manifest schedules[].task (or .name if task empty).
    Return {'text': '...'} to notify user, or None."""
```

Manifest `schedules` section:

```yaml
schedules:
  - name: nightly_consolidation
    cron: "0 3 * * *"
    task: execute_consolidation   # optional; if empty, uses name
  - name: daily_decay
    cron: "0 4 * * *"
    task: execute_decay
```

Loader passes `entry.task_name` (task or name) to `execute_task()`. Extension dispatches internally (e.g. via `match task_name`).

### `ServiceProvider`

Background service with its own loop.

```python
async def run_background(self) -> None:
    """Main loop. Must handle CancelledError."""
```

Loader wraps it in `asyncio.create_task()` and cancels on shutdown.

### MCP Bridge (`mcp` extension)

The MCP Bridge connects to external [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers from config and exposes their tools to the Orchestrator via the SDK's native `mcp_servers`. It does **not** implement `ToolProvider`; MCP tools are injected into the agent after `loader.start_all()` (post-start injection, see [ADR 006](adr/006-mcp-extension.md)).

Configure servers in `config.servers`:

**stdio** (local subprocess):

```yaml
config:
  servers:
    - alias: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]
      cache_tools: true
      tool_filter: ["read_file", "list_directory"]
```

**streamable-http** (HTTP endpoint):

```yaml
config:
  servers:
    - alias: github
      transport: streamable-http
      url: http://localhost:3000/mcp
      headers:
        Authorization: "Bearer ${GITHUB_TOKEN}"
      cache_tools: true
      require_approval:
        always: ["delete_repository"]
```

Secrets: `${SECRET_NAME}` in `url`, `headers`, and `env` is resolved via `context.get_secret()` (keyring/.env per ADR 012). Missing secrets cause that server to be skipped with a log warning.

**MCP Prompts** (Phase 2): Add `prompts` per server to inject MCP prompt templates into the agent context via the ContextProvider chain:

```yaml
    - alias: code_review
      transport: streamable-http
      url: http://localhost:8000/mcp
      prompts:
        - name: code_review_instructions
          args:
            focus: "security vulnerabilities"
            language: "python"
```

Use `prompts: "auto"` to fetch all prompts from the server. Config: `prompts_cache_ttl` (default 300s), `reconnect_interval` (default 120s) for failed server retries.

**Approval flow**: When `require_approval` is set, the Router detects SDK interruptions and emits `system.mcp.tool_approval_request`; channels (e.g. CLI) subscribe, show a prompt to the user, and emit `system.mcp.tool_approval_response` with approve/reject. The run resumes accordingly.

### Web Channel (`web_channel` extension)

The Web Channel extension ([ADR 026](adr/026-web-channel.md)) implements `ChannelProvider`, `StreamingChannelProvider`, and `ServiceProvider` to expose the system over HTTP. It provides:

- OpenAI-compatible endpoints: `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/responses`
- custom REST endpoints under `/api/` for health, threads, projects, and proactive notification polling
- SSE streaming mapped from `StreamingChannelProvider`
- request/response bridging via `RequestBridge` (single active request guard, future/queue correlation, long-poll notifications)

Unlike CLI and Telegram, `web_channel` can pass `thread_id` from the `X-Thread-Id` header into `router.handle_user_message(...)`, which activates named thread pooling in `ThreadManager`.

See [channels.md](channels.md#web-channel) and [api/openapi.yaml](api/openapi.yaml).

### Web Search (`web_search` extension)

The Web Search extension ([ADR 013](adr/013-web-search.md)) implements `ToolProvider` and exposes two tools: **`web_search`** (query → ranked results with snippets) and **`open_page`** (fetch full page content from URLs). It uses configurable search and read providers (DuckDuckGo, Jina, Tavily, Perplexity, SearXNG, etc.). Add `web_search` to the Orchestrator's tool set (e.g. via `uses_tools` or by having the extension in the default capabilities) so the agent can search the web and read pages regardless of LLM provider.

### Inbox (`inbox` extension)

The Inbox extension ([ADR 024](adr/024-unified-inbox.md)) provides unified storage for incoming data from external systems. Source extensions (mail, github, gitlab, etc.) persist records via the Inbox service API (`depends_on: [inbox]` + `context.get_extension("inbox")`). Tools: **`inbox_list`** (list/filter items by source, entity type, status) and **`inbox_read`** (read a single item by `inbox_id`). After each successful write, Inbox emits `inbox.item.ingested` on the Event Bus; consumers (e.g. triage agent) subscribe and call `get_item(inbox_id)` to fetch full payload.

### `ContextProvider`

Enriches agent context before each invocation. Multiple ContextProviders coexist; the kernel calls them in `context_priority` order (lower = earlier).

```python
from core.extensions.contract import TurnContext

@property
def context_priority(self) -> int:
    """Lower value = earlier in chain. Default: 100."""

async def get_context(self, prompt: str, turn_context: TurnContext) -> str | None:
    """Return context string to inject, or None to skip."""
```

`TurnContext` is a frozen dataclass with: `agent_id`, `channel_id`, `user_id`, `thread_id`. The kernel passes it on every invocation so providers can tailor context (e.g. filter by channel).

Wired by `loader.wire_context_providers()` after `start_all()`. The middleware concatenates all non-empty results with `---` separators and returns a **context string** (not an enriched user message).

**Built-in provider:** `_ActiveChannelContextProvider` (priority 0) injects `[Current Thread Context]` with channel identity and narrative instructions so the agent knows which channel the user is on.

**Two public behaviors:**

- **invoke_agent** / **invoke_agent_streamed**: Context is injected into the **system** role via `agent.clone(instructions=...)`; the user message is unchanged.
- **enrich_prompt**: Returns a single string `context + separator + prompt` for downstream agents that receive one combined prompt.

**Example:** The `memory` extension implements ContextProvider to inject relevant context via intent-aware hybrid search (FTS5 + vector + graph traversal + RRF).

### `SetupProvider`

Extension that needs configuration (secrets, settings). The Loader detects SetupProvider extensions at startup, calls `on_setup_complete()` to determine configured state, and injects `setup_instructions` from the manifest into the Orchestrator's capabilities summary when an extension is unconfigured.

**Setup flow:**

1. At startup, the Loader calls `on_setup_complete()` for each SetupProvider. If it returns `(False, msg)`, the extension is "unconfigured".
2. The capabilities summary includes an "Extensions needing setup" section with `manifest.setup_instructions` for unconfigured extensions.
3. The Orchestrator can use the `configure_extension(extension_id, param_name, value)` core tool to apply configuration. The tool calls `apply_config()` then `on_setup_complete()` and returns a structured result.
4. For secrets (e.g. API tokens), the agent should use `request_secure_input` first to collect the value securely, then pass it to `configure_extension` or have the user provide it via a secure channel.

```python
def get_setup_schema(self) -> list[dict]:
    """[{name, description, secret, required}]"""

async def apply_config(self, name: str, value: str) -> None:
    """Save config value."""

async def on_setup_complete(self) -> tuple[bool, str]:
    """Verify setup. Return (success, message)."""
```

---

## Manifest Schema

File: `sandbox/extensions/<id>/manifest.yaml`

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Unique extension identifier (directory name) |
| `name` | str | Human-readable name |
| `entrypoint` | str \| null | `module:ClassName` for programmatic extensions; omit for declarative agents |
| `agent` | object \| null | Agent config; if present and no `entrypoint`, creates `DeclarativeAgentAdapter` |

**Rule:** Either `entrypoint` or `agent` must be present. For non-agent extensions, `entrypoint` is required.

### Optional Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | str | `"1.0.0"` | Semantic version |
| `description` | str | `""` | Shown in Orchestrator capabilities summary |
| `setup_instructions` | str | `""` | Instructions for the agent when extension is unconfigured. Shown in capabilities summary; agent uses `configure_extension` or `request_secure_input` + `request_restart` per instructions. |
| `depends_on` | list[str] | `[]` | Extension IDs; load order is topological |
| `secrets` | list[str] | `[]` | Required env var names |
| `config` | dict | `{}` | Passed to `context.get_config()` |
| `enabled` | bool | `true` | If false, extension is skipped |
| `agent_id` | str | `id` | ModelRouter agent key; defaults to extension id |
| `agent_config` | dict | null | Per-agent model config for ModelRouter |
| `events` | object | null | `publishes` (docs only), `subscribes` (Loader wiring) |
| `schedules` | list | `[]` | For SchedulerProvider: `[{name, cron, task?}]`; Loader calls `execute_task(entry.task_name)` per cron |

### Agent Section (`agent`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `integration_mode` | `"tool"` \| `"handoff"` | `"tool"` | How Orchestrator uses the agent |
| `model` | str | — | LLM model identifier |
| `instructions` | str | `""` | Inline system prompt (optional). Merged with `prompt.jinja2` if both exist. |
| `parallel_tool_calls` | bool | `false` | Allow concurrent tool calls within one model turn |
| `parameters` | dict | `{}` | Extra agent params |
| `uses_tools` | list[str] | `[]` | Extension IDs or `core_tools` for tools |
| `limits` | object | defaults below | `max_turns`, `max_tokens_per_invocation`, `time_budget_ms` |

**Limits defaults:** `max_turns=10`, `max_tokens_per_invocation=50000`, `time_budget_ms=120000`.

**Prompt resolution:** Agent extensions may have `prompt.jinja2` in `extensions/<id>/`. The Loader auto-detects it at startup (no manifest field). If present, file content is used first; then `instructions` from manifest is appended. Only extension dir is searched; `sandbox/prompts/` is system-only.

### Events Section (`events`)

| Sub-field | Purpose |
|-----------|---------|
| `publishes` | **Documentation only.** Topics the extension emits. |
| `subscribes` | **Loader wiring.** Topics and handlers. |

**Subscribe handlers:**

| handler | Behavior |
|---------|----------|
| `notify_user` | Send `event.payload["text"]` to user via default channel |
| `invoke_agent` | Invoke this extension's AgentProvider; send response to user (proactive flow) |
| `custom` | No auto-wiring; extension must call `ctx.subscribe_event()` in `initialize()` |

---

## ExtensionContext API

Extensions receive `ExtensionContext` in `initialize()`. All interaction with the kernel goes through it.

### Lifecycle & Identity

| Member | Description |
|--------|-------------|
| `extension_id` | Extension id from manifest |
| `config` | Merged config: `settings.extensions.<id>` overrides + manifest `config` |
| `logger` | `create_subsystem_logger(f"ext.{extension_id}")` — [`SubsystemLogger`](../core/logging_config.py): `.child()`, `.is_enabled()`, optional `meta=` on level methods; see [ADR 036](adr/036-subsystem-logging.md) |
| `data_dir` | `sandbox/data/<extension_id>/` (created on access) |

### Configuration & Dependencies

| Method | Description |
|--------|-------------|
| `get_config(key, default)` | Read config. Resolution order: `settings.yaml` → `extensions.<id>.<key>`, then manifest `config.<key>`, then `default` |
| `get_secret(name)` | Read secret by name (keyring first, then `.env`). Async; use `await ctx.get_secret(name)`. |
| `get_extension(ext_id)` | Get another extension instance **only if** in `depends_on` |
| `list_threads()` / `get_thread()` / `create_thread()` / `update_thread()` / `archive_thread()` / `get_thread_history()` | Persistent thread metadata and history access |
| `list_projects()` / `get_project()` / `create_project()` / `update_project()` / `delete_project()` | Persistent project management |

**Extension class `ConfigModel` (optional):** On the extension entrypoint class, set `ConfigModel` to a Pydantic `BaseModel` matching the merged manifest + `settings.extensions.<id>` keys (e.g. `model_config = ConfigDict(extra="forbid")` for strict validation). The Loader validates before `initialize()`. Validation failures mark only that extension as `ERROR`; other extensions continue bootstrapping. The failure is recorded in the Loader diagnostics registry and exposed via the `extensions_doctor` tool. See [ADR 035](adr/035-pydantic-settings-models.md).

### Event Bus

| Method | Description |
|--------|-------------|
| `emit(topic, payload, correlation_id)` | Publish event (fire-and-forget) |
| `subscribe_event(topic, handler)` | Subscribe to topic; handler receives `Event` |
| `notify_user(text, channel_id)` | Send message to user (emits `system.user.notify`) |
| `request_agent_task(prompt, channel_id)` | Ask Orchestrator; response to user |
| `request_agent_background(prompt, correlation_id)` | Ask Orchestrator silently |

### Agent Invocation

| Method | Description |
|--------|-------------|
| `invoke_agent(prompt)` | Run Orchestrator with prompt, return response |
| `invoke_agent_streamed(prompt, on_chunk, on_tool_call)` | Run Orchestrator with streaming callbacks; returns final text. For proactive extensions that want incremental delivery. |
| `enrich_prompt(prompt, agent_id)` | Apply ContextProvider chain; returns context + separator + prompt for use as a single prompt by downstream agents. For invoke_agent, context is injected into system role instead. |
| `on_user_message` | Alias for `router.handle_user_message` (full message cycle, including optional `thread_id`) |

### System Control

| Method | Description |
|--------|-------------|
| `request_restart()` | Write `sandbox/.restart_requested` for supervisor |
| `request_shutdown()` | Set shutdown event |

### Agent Extensions Only

| Member | Description |
|--------|-------------|
| `resolved_tools` | Tools from `uses_tools` (ToolProvider extensions + `core_tools`) |
| `resolved_instructions` | Combined from `prompt.jinja2` (if present in extension dir) + `instructions` |
| `agent_model` | Model from manifest |
| `model_router` | `ModelRouter` for `get_model(agent_id)` |
| `agent_id` | Agent id for model resolution |

---

## Interaction with Event Bus

See [event_bus.md](event_bus.md) for full details.

### Flow: User Message → Agent → Channel

1. **Channel** (e.g. `cli_channel`) emits `user.message` with `{text, user_id, channel_id}`
2. **Kernel** subscribes to `user.message`; calls `router.handle_user_message()`
3. **MessageRouter** invokes Orchestrator; sends response via `channel.send_to_user()`

`web_channel` may also include `thread_id` in the event payload; the router then selects or creates a named runtime thread instead of using the default rotated thread.

### Flow: Proactive Agent (e.g. Reminders)

1. **Scheduler** (or another extension) emits `reminder.due` when a reminder fires
2. **Loader** wires `invoke_agent` for extensions that subscribe to `reminder.due`
3. **AgentProvider.invoke()** runs; response is sent to user via `notify_user`

### System Topics

| Topic | Payload | Handler |
|-------|---------|---------|
| `system.user.notify` | `{text, channel_id?}` | Deliver to user |
| `system.agent.task` | `{prompt, channel_id?}` | Invoke Orchestrator; response to user |
| `system.agent.background` | `{prompt, correlation_id?}` | Invoke Orchestrator silently |

---

## Interaction with Orchestrator

The **Orchestrator** is the main agent that coordinates user requests.

- **Tools:** Core tools (`core/tools/`) + `loader.get_all_tools()` (ToolProvider) + delegation tools (`list_agents`, `delegate_task`, `create_agent`, `list_models`, `list_available_tools`)
- **Instructions:** Include `loader.get_capabilities_summary()` — natural-language list of tools; agents are available on-demand via `list_agents`
- **Routing:** Orchestrator discovers agents via `list_agents` (with cost/capability metadata) and delegates via `delegate_task`. Cost-aware: prefers lower-cost agents for simple tasks, higher-capability agents for complex ones.
- **Dynamic agents:** `create_agent` accepts only extension IDs in `tools` (no function aliases). Semantics:
  - `tools=null` → invalid (must pass explicit IDs or `[]`)
  - `tools=[]` → explicitly create an agent without tools
  - `tools=[...]` → strict validation; unknown IDs fail the call
  - `parallel_tool_calls` defaults to `false`; set `true` to allow concurrent tool calls for that created agent
- **Tool discovery:** `list_available_tools` returns IDs plus descriptions to help the Orchestrator choose tools before `create_agent`.

Agent extensions are registered in `AgentRegistry` at startup. The Orchestrator discovers them via `list_agents` and invokes them via `delegate_task` — agent descriptions are not in the system prompt, loaded on-demand. See [ADR 017](adr/017-agents-registry.md) and [ADR 019](adr/019-cost-capability-routing.md).

---

## Declarative vs Programmatic Agents

### Declarative Agent

No `main.py`. Defined entirely in manifest:

```yaml
id: simple_agent
name: Simple Agent
description: A simple agent for testing

agent:
  integration_mode: tool
  model: gpt-5-mini
  parallel_tool_calls: false
  instructions: |
    Always reply in the format of a haiku
  uses_tools:
    - kv
  limits:
    max_turns: 3

depends_on:
  - kv
enabled: true
```

Loader creates `DeclarativeAgentAdapter`, which builds an `Agent` from `resolved_instructions`, `resolved_tools`, and `model`.

### Programmatic Agent

Has `entrypoint` and custom Python class implementing `AgentProvider` for full control (custom invoke logic, handoff, etc.).

---

## Creating a New Extension

### 1. Directory Structure

```
sandbox/extensions/my_extension/
├── __init__.py      # required (regular package)
├── manifest.yaml
├── main.py          # required if entrypoint is main:MyExtension
├── tools.py         # optional
├── prompt.jinja2    # optional
└── README.md        # optional
```

Import rules:

- Use package imports (for example `from sandbox.extensions.my_extension.tools import ...`).
- Do not mutate `sys.path` in extension runtime code.
- Do not use `spec_from_file_location()` or other file-based import fallbacks in extension runtime code.

### 2. Minimal manifest.yaml

```yaml
id: my_extension
name: My Extension
version: "1.0.0"
entrypoint: main:MyExtension
description: |
  What this extension does. Shown to Orchestrator.
depends_on: []        # e.g. ["kv"] if you need KV store
config: {}
enabled: true
```

### 3. Minimal Extension Class

```python
# sandbox/extensions/my_extension/main.py
from typing import Any

class MyExtension:
    def __init__(self) -> None:
        self._ctx: Any = None

    async def initialize(self, context: Any) -> None:
        self._ctx = context

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        pass

    def health_check(self) -> bool:
        return True
```

### 4. Adding ToolProvider

**Rule:** All agent tools MUST return structured output (Pydantic model or fixed-shape dict), never bare strings. See [agent_tools skill](.cursor/skills/agent_tools/SKILL.md).

```python
from agents import function_tool
from pydantic import BaseModel

class MyToolResult(BaseModel):
    success: bool
    message: str = ""
    error: str | None = None

class MyExtension:
    # ... lifecycle methods ...

    def get_tools(self) -> list[Any]:
        @function_tool(name_override="my_tool")
        async def my_tool(arg: str) -> MyToolResult:
            """Tool description for the LLM."""
            if not arg:
                return MyToolResult(success=False, error="arg required")
            return MyToolResult(success=True, message=f"Result: {arg}")
        return [my_tool]
```

### 5. Using Another Extension

Add to `depends_on` in manifest, then:

```python
kv = self._ctx.get_extension("kv")
value = await kv.get("key")
```

`depends_on` is a **hard contract**: if the dependency fails to load, initialize, or start, your extension is cascaded to ERROR and never runs. `get_extension()` raises `RuntimeError` (never returns `None`) when a declared dependency is unavailable.

### 6. Publishing Events

```python
await self._ctx.emit("my_extension.done", {"result": "ok"})
```

### 7. Subscribing to Events

**Manifest-driven (notify_user):**

```yaml
events:
  subscribes:
    - topic: alert.urgent
      handler: notify_user
```

**Custom handler (in code):**

```python
async def initialize(self, context: Any) -> None:
    self._ctx = context
    self._ctx.subscribe_event("alert.urgent", self._on_alert)

async def _on_alert(self, event) -> None:
    text = event.payload.get("text", "")
    await self._ctx.notify_user(text)
```

---

## Dependency Order

- `depends_on` defines load order (topological sort) and is a **hard contract**
- Missing dependency raises `ValueError`
- Cycle in `depends_on` raises `ValueError`
- If a dependency fails to load, initialize, or start, dependents are cascaded to ERROR (never initialized or started)
- `get_extension(ext_id)` raises `ValueError` if `ext_id` is not in `depends_on`; raises `RuntimeError` if the dependency is not loaded or in ERROR state (never returns `None`)

---

## Health Check

Loader runs `health_check()` every 30 seconds. If it returns `False` or raises, the extension is marked `ERROR` and `stop()` is called. The failure is also recorded in the Loader diagnostics registry and emitted on the Event Bus as `system.extension.error`. Implement `health_check()` for extensions with background tasks or external connections.

---

## Diagnostics

Loader keeps a bounded in-memory diagnostic history for each extension. Diagnostics capture:

- phase: `load`, `config_validate`, `initialize`, `start`, or `health_check`
- reason: `import_error`, `config_invalid`, `init_error`, `start_error`, `dependency_failed`, or `health_check_failed`
- message, traceback, and dependency chain where relevant

Dependency cascades still transition dependents to `ExtensionState.ERROR`, but the diagnostic `reason` distinguishes a direct failure from a skipped dependency.

Two diagnostic surfaces are available:

- Loader report APIs such as `get_extension_status_report()` and `get_failed_extensions()`
- the `extensions_doctor` core tool for the Orchestrator and other agents using `core_tools`

Failed extensions do not appear in the normal capabilities summary by default; the agent must explicitly inspect diagnostics when needed.

---

## References

- [architecture.md](architecture.md) — System overview and bootstrap
- [boundary-checks.md](boundary-checks.md) — Architecture boundary enforcement scripts
- [event_bus.md](event_bus.md) — Event Bus architecture and topics
- [channels.md](channels.md) — Channel providers (CLI, Telegram, Web)
- [scheduler.md](scheduler.md) — Scheduler extension
- [ADR 004: Event Bus](adr/004-event-bus.md) — Design decisions
- `core/extensions/` — Contract, manifest, context, `loader/`, `routing/`, `persistence/`
- `sandbox/extensions/` — Extensions: `cli_channel`, `telegram_channel`, `web_channel`, `memory`, `kv`, `scheduler`, `task_engine`, `web_search`, `mcp`, `shell_exec`, `embedding`, `inbox`, `builder_agent`, `simple_agent`

