# System Architecture

High-level overview of the assistant4 system: entry points, bootstrap flow, and how core components interact.

---

## Overview

assistant4 is an **AI agent platform** built around an extensible kernel. The system processes user messages through channels, invokes the Orchestrator agent with context from memory, and delivers responses back to the user. Extensions provide tools, channels, agents, schedulers, and services.

**Key principles:**

- **Extensions only** — All user-facing features (channels, memory, heartbeat, scheduler) are extensions. Core provides the kernel.
- **Event-driven** — Event Bus for durable pub/sub; MessageRouter for hot-path user→agent→channel flow.
- **Protocol-based** — Extensions declare capabilities via `@runtime_checkable` Protocol classes (`core/extensions/contract.py`), detected at load time with `isinstance`. No manifest field needed.

---

## Entry Points

| Entry | Command | Purpose |
|-------|---------|---------|
| **Supervisor** | `python -m supervisor` | Production entry. Spawns and monitors the agent process; supports restart-by-file and crash recovery. |
| **Core (agent)** | `python -m core` | AI agent process. Bootstrap Loader, EventBus, Orchestrator; extensions run the UI. |

Users typically run `python -m supervisor`. The Supervisor spawns `python -m core` as a child process. See [ADR 001](adr/001-supervisor-agent-processes.md).

---

## Bootstrap Flow (Runner)

The agent process bootstrap in `core/runner.py`:

```
 1. load_settings()                        → config/settings.yaml
 2. setup_logging()
 3. ModelRouter(settings, secrets_getter)
 4. Loader(extensions_dir, data_dir)
    └─ set_shutdown_event, set_model_router
 5. MessageRouter()
 6. EventBus(db_path, poll_interval, batch_size) → recover()
    └─ loader.set_event_bus()
 7. loader.discover()                      — scan sandbox/extensions/ for manifest.yaml
 8. loader.load_all()                      — topological sort by depends_on; instantiate
 9. loader.initialize_all(router)
10. loader.detect_and_wire_all(router)     — ToolProvider, ChannelProvider, etc.
11. loader.wire_event_subscriptions(event_bus)
12. create_orchestrator_agent()            — core tools + extension tools + agent tools
    └─ router.set_agent()
13. event_bus.start()
14. loader.start_all()
15. SQLiteSession → router.set_session()   — conversation history
16. loader.wire_context_providers(router)
17. shutdown_event.wait()
```

Shutdown: `event_bus.stop()` → `loader.shutdown()` (reverse dependency order: `stop()` → `destroy()`).

---

## Component Diagram

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                          Supervisor (python -m supervisor)                     │
│  Spawns core; polls restart file; restarts on crash (with backoff limit)       │
└────────────────────────────────────────────────────────────────────────────────┘
                                         │
                                         ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                          Core (python -m core)                                 │
│  Runner → Loader + EventBus + MessageRouter + ModelRouter + Orchestrator       │
└────────────────────────────────────────────────────────────────────────────────┘
     │              │                │                │                │
     ▼              ▼                ▼                ▼                ▼
┌───────────┐ ┌────────────┐ ┌───────────────┐ ┌─────────────┐ ┌──────────────┐
│  Loader   │ │  EventBus  │ │ MessageRouter │ │ ModelRouter │ │ Orchestrator │
│ -discover │ │ -journal   │ │ -channels     │ │ -providers  │ │ -core tools  │
│ -load     │ │ -dispatch  │ │ -invoke_agent │ │ -get_model  │ │ -ext tools   │
│ -wire     │ │ -recovery  │ │ -middleware   │ │ -caching    │ │ -agent tools │
└───────────┘ └────────────┘ └───────────────┘ └─────────────┘ └──────────────┘
     │              │                │                │                │
     └──────────────┴────────────────┴────────────────┴────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                      Extensions (sandbox/extensions/<id>/)                       │
│  cli_channel │ telegram_channel │ memory │ scheduler │ kv │ heartbeat │ embedding │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### Protocols (contract.py)

- **Location:** `core/extensions/contract.py`
- **Role:** Defines all extension capability interfaces as `@runtime_checkable` Protocol classes. Loader detects capabilities via `isinstance(ext, Protocol)`.

| Protocol | Method(s) | Purpose |
|----------|-----------|---------|
| `Extension` (base) | `initialize`, `start`, `stop`, `destroy`, `health_check` | Lifecycle |
| `ToolProvider` | `get_tools()` | Provides `@function_tool` objects for the agent |
| `ChannelProvider` | `send_to_user(user_id, message)`, `send_message(text)` | User communication channel |
| `StreamingChannelProvider` | `on_stream_start`, `on_stream_chunk`, `on_stream_status`, `on_stream_end` | Incremental response delivery (optional; see [ADR 010](adr/010-streaming.md)) |
| `AgentProvider` | `get_agent_descriptor()`, `invoke(task, context)` | Specialized AI agent |
| `SchedulerProvider` | `execute_task(task_name)` | Cron-driven periodic tasks |
| `ServiceProvider` | `run_background()` | Long-running background service |
| `ContextProvider` | `get_context(prompt, agent_id)`, `context_priority` | Enriches prompt before each agent invocation |
| `SetupProvider` | `get_setup_schema()`, `apply_config()`, `on_setup_complete()` | Interactive configuration |

Supporting data classes: `AgentDescriptor`, `AgentResponse`, `AgentInvocationContext`.

### Loader

- **Location:** `core/extensions/loader.py`
- **Role:** Discovers extensions, loads them in dependency order, initializes, wires protocols, manages lifecycle.
- **Wiring:** Detects all Protocol implementors above and registers them with the MessageRouter or internal registries.

### EventBus

- **Location:** `core/events/bus.py`
- **Role:** Durable pub/sub. Events persisted to SQLite before delivery; at-least-once semantics.
- **See:** [event_bus.md](event_bus.md)

### MessageRouter

- **Location:** `core/extensions/router.py`
- **Role:** Routes user messages to the Orchestrator; delivers responses to channels. In-memory pub/sub for `user_message` and `agent_response`. ContextProvider middleware enriches prompts before agent invocation.
- **Key details:** `asyncio.Lock` serializes concurrent agent invocations; `SQLiteSession` stores conversation history; `notify_user()` enables proactive messages.
- **Streaming:** If the channel implements `StreamingChannelProvider`, `handle_user_message()` uses `invoke_agent_streamed()` and calls the channel's stream lifecycle (`on_stream_start` → `on_stream_chunk` / `on_stream_status` → `on_stream_end`). Otherwise it uses `invoke_agent()` and `send_to_user()` as before. See [ADR 010](adr/010-streaming.md) and [channels.md](channels.md#streaming).

### Core Tools

- **Location:** `core/tools/`
- **Role:** Built-in tools available to the Orchestrator and declarative agents via `CoreToolsProvider`.
- **Tools:** `file` (read/write files), `apply_patch_tool` (patch files), `request_restart` (trigger system restart), `list_channels` / `send_to_channel` (channel selection; see [channels.md](channels.md)), `shell_tool` (shell execution; hosted-only), `WebSearchTool` (web search; hosted-only).
- **Hosted-only gating:** `shell_tool` and `WebSearchTool` are included only when the agent's provider supports OpenAI hosted tools (`supports_hosted_tools` in provider config).

### Orchestrator

- **Location:** `core/agents/orchestrator.py`
- **Role:** Main AI agent. Created via `create_orchestrator_agent()` factory with:
  - **core tools** — from `CoreToolsProvider` (file, shell, restart, web search)
  - **extension tools** — from ToolProvider extensions (`loader.get_all_tools()`)
  - **agent tools** — from AgentProvider extensions in `tool` mode (`loader.get_agent_tools()`)
  - **capabilities_summary** — natural-language summary injected into the prompt template
  - **instructions** — resolved from `agents.orchestrator.instructions` in settings (supports Jinja2 templates)
  - **model** — resolved via `model_router.get_model("orchestrator")`

### ModelRouter

- **Location:** `core/llm/router.py`
- **Role:** Resolves `agent_id` → cached Model instance via `config/settings.yaml`. Supports multiple providers:
  - `openai` / `openai_compatible` — OpenAI, LM Studio, OpenRouter, etc.
  - `anthropic` — Claude models via Anthropic API.
- **Features:** per-agent model caching, `supports_hosted_tools()` check, provider health checks, dynamic agent config registration from extension manifests.
- **See:** [llm.md](llm.md)

---

## Data Flow: User Message → Response

```
Channel (cli_channel / telegram_channel)
  │  ctx.emit("user.message", {text, user_id, channel_id})
  ▼
EventBus  (journal → dispatch)
  │  kernel_user_message_handler (wired in loader.wire_event_subscriptions)
  │  resolves channel_id → ChannelProvider
  ▼
router.handle_user_message(text, user_id, channel)
  ├─ _emit("user_message")                → Memory.save_episode (subscriber)
  ├─ if channel is StreamingChannelProvider:
  │    ├─ channel.on_stream_start(user_id)
  │    ├─ invoke_agent_streamed(text, on_chunk, on_tool_call)
  │    │    ├─ invoke_middleware(prompt)
  │    │    └─ Runner.run_streamed() → on_chunk (deltas), on_tool_call (tool name)
  │    └─ channel.on_stream_end(user_id, full_text)
  ├─ else:
  │    ├─ invoke_agent(text)
  │    │    ├─ invoke_middleware(prompt)   → ContextProvider chain (Memory.get_context)
  │    │    └─ Runner.run(agent, prompt, session=SQLiteSession)
  │    └─ channel.send_to_user(user_id, response)
  ├─ _emit("agent_response")              → Memory.save_episode (subscriber)
  └─ (response already delivered by channel)
```

See [event_bus-memory-flow.md](event_bus-memory-flow.md) for detailed flow and [ADR 010](adr/010-streaming.md) for streaming design.

---

## Extension Categories

| Category | Extensions | Protocols | Purpose |
|----------|-----------|-----------|---------|
| **Channels** | cli_channel, telegram_channel | ChannelProvider, ServiceProvider | Receive user input; deliver agent responses |
| **Tools** | kv, scheduler | ToolProvider | Tools for Orchestrator (kv_set/kv_get, schedule_once, etc.) |
| **Agents** | builder_agent, simple_agent | AgentProvider | Specialized agents invoked as tools (`integration_mode: tool`) |
| **Memory** | memory | ToolProvider, ContextProvider, SchedulerProvider | Graph-based cognitive memory: episodes, facts, procedures, opinions. Intent-aware hybrid retrieval, LLM-powered consolidation, Ebbinghaus decay |
| **Proactive** | heartbeat | SchedulerProvider | Periodic Scout → Orchestrator escalation (every 2 min) |
| **Infrastructure** | embedding | (internal API) | Embedding generation for memory and other extensions |

---

## References

- [extensions.md](extensions.md) — Extension architecture and manifest
- [event_bus.md](event_bus.md) — Event Bus
- [event_bus-memory-flow.md](event_bus-memory-flow.md) — Detailed message flow
- [memory.md](memory.md) — Memory system
- [heartbeat.md](heartbeat.md) — Agent loop
- [llm.md](llm.md) — Model routing
- [channels.md](channels.md) — Channel extensions
- [scheduler.md](scheduler.md) — Scheduler extension
- [configuration.md](configuration.md) — Settings reference
