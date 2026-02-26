# Extensions System

This document describes the extension architecture in the assistant4 system: how extensions work, their types, interactions with the Event Bus, core kernel, and Orchestrator, plus manifest schema and practical guidance for developers.

---

## Overview

Extensions are **pluggable modules** that extend the system with tools, channels, agents, schedulers, and services. They live in `sandbox/extensions/<id>/` and are discovered, loaded, and wired by the **Loader** at startup. Capabilities are detected via **`@runtime_checkable` Protocol classes** (`core/extensions/contract.py`), not via manifest fields.

**Key principle:** Extensions interact with the system **only** through `ExtensionContext` — no direct imports of core modules.

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
│  - extensions    │         │  - agent ref     │         │  - system topics    │
│  - protocol      │         │  - notify_user   │         │  - recovery         │
│    detection     │         │  - invoke_agent  │         │                     │
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
│   Orchestrator   │  ← Agent with extension tools + agent-tools (specialized agents)
│  (core/agents)   │
└──────────────────┘
```

---

## Bootstrap Flow (Runner)

The startup sequence in `core/runner.py`:

1. **discover** — Scan `sandbox/extensions/` for `manifest.yaml`; load manifests, filter `enabled: true`
2. **load_all** — Topological sort by `depends_on`; dynamic import or `DeclarativeAgentAdapter`; instantiate
3. **initialize_all** — Create `ExtensionContext` per extension; call `initialize(ctx)`
4. **detect_and_wire_all** — `isinstance(ext, Protocol)`; wire ToolProvider, ChannelProvider, AgentProvider, SchedulerProvider
5. **wire_event_subscriptions** — Wire manifest-driven `notify_user` / `invoke_agent`; kernel `user.message` handler
6. **create_orchestrator_agent** — Merge core tools + `get_all_tools()` + `get_agent_tools()` + capabilities summary
7. **start** — EventBus, then `loader.start_all()` (extensions' `start()`, ServiceProvider tasks, cron + health loops)
8. **SQLiteSession** → `router.set_session()` (conversation history)
9. **wire_context_providers** — Collect `ContextProvider` extensions, chain into router middleware

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

- **integration_mode: "tool"** — Wrapped as a tool for the Orchestrator via `get_agent_tools()`
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

`TurnContext` is a frozen dataclass with: `agent_id`, `channel_id`, `user_id`, `session_id`. The kernel passes it on every invocation so providers can tailor context (e.g. filter by channel).

Wired by `loader.wire_context_providers()` after `start_all()`. The middleware concatenates all non-empty results with `---` separators and returns a **context string** (not an enriched user message).

**Built-in provider:** `_ActiveChannelContextProvider` (priority 0) injects `[Current Session Context]` with channel identity and narrative instructions so the agent knows which channel the user is on.

**Two public behaviors:**

- **invoke_agent** / **invoke_agent_streamed**: Context is injected into the **system** role via `agent.clone(instructions=...)`; the user message is unchanged.
- **enrich_prompt**: Returns a single string `context + separator + prompt` for downstream agents that receive one combined prompt.

**Example:** The `memory` extension implements ContextProvider to inject relevant context via intent-aware hybrid search (FTS5 + vector + graph traversal + RRF).

### `SetupProvider`

Extension that needs configuration (secrets, settings).

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
| `setup_instructions` | str | `""` | User-facing setup help |
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
| `parameters` | dict | `{}` | Extra agent params |
| `uses_tools` | list[str] | `[]` | Extension IDs or `core_tools` for tools |
| `limits` | object | defaults below | `max_turns`, `max_tokens_per_invocation`, `time_budget_ms` |

**Limits defaults:** `max_turns=10`, `max_tokens_per_invocation=50000`, `time_budget_ms=120000`.

**Prompt resolution:** Agent extensions may have `prompt.jinja2` in `extensions/<id>/`. The Loader auto-detects it at startup (no manifest field). If present, file content is used first; then `instructions` from manifest is appended. Only extension dir is searched; project `prompts/` is system-only.

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
| `logger` | `logging.getLogger(f"ext.{extension_id}")` |
| `data_dir` | `sandbox/data/<extension_id>/` (created on access) |

### Configuration & Dependencies

| Method | Description |
|--------|-------------|
| `get_config(key, default)` | Read config. Resolution order: `settings.yaml` → `extensions.<id>.<key>`, then manifest `config.<key>`, then `default` |
| `get_secret(name)` | Read from environment (e.g. `.env`) |
| `get_extension(ext_id)` | Get another extension instance **only if** in `depends_on` |

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
| `on_user_message` | Alias for `router.handle_user_message` (full message cycle) |

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

- **Tools:** Core tools (`core/tools/`) + `loader.get_all_tools()` (ToolProvider) + `loader.get_agent_tools()` (AgentProvider with `integration_mode: "tool"`)
- **Instructions:** Include `loader.get_capabilities_summary()` — natural-language list of tools and agents
- **Routing:** Orchestrator chooses which tool or agent to call based on the user message and capabilities

Agent extensions in `tool` mode are wrapped as callable tools; the Orchestrator invokes them when it decides a specialized agent is needed.

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
├── manifest.yaml
└── main.py          # if entrypoint is main:MyExtension
```

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

```python
from agents import function_tool

class MyExtension:
    # ... lifecycle methods ...

    def get_tools(self) -> list[Any]:
        @function_tool(name_override="my_tool")
        async def my_tool(arg: str) -> str:
            """Tool description for the LLM."""
            return f"Result: {arg}"
        return [my_tool]
```

### 5. Using Another Extension

Add to `depends_on` in manifest, then:

```python
kv = self._ctx.get_extension("kv")
if kv:
    value = await kv.get("key")
```

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

- `depends_on` defines load order (topological sort)
- Missing dependency raises `ValueError`
- Cycle in `depends_on` raises `ValueError`
- `get_extension(ext_id)` returns `None` if `ext_id` is not in `depends_on`

---

## Health Check

Loader runs `health_check()` every 30 seconds. If it returns `False`, the extension is marked `ERROR` and `stop()` is called. Implement `health_check()` for extensions with background tasks or external connections.

---

## References

- [architecture.md](architecture.md) — System overview and bootstrap
- [event_bus.md](event_bus.md) — Event Bus architecture and topics
- [channels.md](channels.md) — Channel providers (CLI, Telegram)
- [scheduler.md](scheduler.md) — Scheduler extension
- [ADR 004: Event Bus](adr/004-event-bus.md) — Design decisions
- `core/extensions/` — Contract, loader, manifest, context, router
- `sandbox/extensions/` — Extensions: `cli_channel`, `telegram_channel`, `memory`, `kv`, `scheduler`, `task_engine`, `embedding`, `builder_agent`, `simple_agent`
