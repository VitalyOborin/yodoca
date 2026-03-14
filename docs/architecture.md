# System Architecture

High-level overview of the assistant4 system: entry points, bootstrap flow, and how core components interact.

---

## Overview

assistant4 is an **AI agent platform** built around an extensible kernel. The system processes user messages through channels, invokes the Orchestrator agent with context from memory plus persistent thread/project state, and delivers responses back to the user. Extensions provide tools, channels, agents, schedulers, and services.

**Key principles:**

- **Extensions only** — All user-facing features (channels, memory, scheduler) are extensions. Core provides the kernel.
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
12. AgentRegistry + ModelCatalog
13. create_orchestrator_agent()            — core + extension + delegation tools
    └─ router.set_agent()
14. router.configure_thread(...)          — default runtime thread + persistence services
15. loader.wire_context_providers(router)
16. event_bus.start()
17. loader.start_all()
18. shutdown_event.wait()
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
│  Runner → Loader + EventBus + MessageRouter + ModelRouter + AgentRegistry      │
│        + ModelCatalog + Orchestrator                                          │
└────────────────────────────────────────────────────────────────────────────────┘
     │              │                │                │                │
     ▼              ▼                ▼                ▼                ▼
┌───────────┐ ┌────────────┐ ┌───────────────┐ ┌─────────────┐ ┌──────────────┐
│  Loader   │ │  EventBus  │ │ MessageRouter │ │ ModelRouter │ │ Orchestrator │
│ -discover │ │ -journal   │ │ -channels     │ │ -providers  │ │ -core tools  │
│ -load     │ │ -dispatch  │ │ -invoke_agent │ │ -get_model  │ │ -ext tools   │
│ -wire     │ │ -recovery  │ │ -threads     │ │ -caching    │ │ -deleg tools │
│ -lifecycle│ │            │ │ -delivery     │ │             │ │              │
└───────────┘ └────────────┘ └───────────────┘ └─────────────┘ └──────────────┘
                                                      │
                                        ┌─────────────┴──────────────┐
                                        │ AgentRegistry │ModelCatalog│
                                        │ -register     │-get_info   │
                                        │ -invoke       │-list_models│
                                        │ -list_agents  │-cost tiers │
                                        └────────────────────────────┘
     │              │                │                │                │
     └──────────────┴────────────────┴────────────────┴────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                      Extensions (sandbox/extensions/<id>/)                       │
│ cli_channel │ telegram_channel │ web_channel │ memory │ scheduler │ kv │ embedding │ task_engine │
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

- **Location:** `core/extensions/loader/loader.py`
- **Role:** Discovers extensions, loads them in dependency order, initializes, wires protocols, manages lifecycle.
- **Wiring:** Detects all Protocol implementors above and registers them with the MessageRouter or internal registries.
- **Internal split:** `core/extensions/loader/` now separates manifest discovery, dependency resolution, extension instantiation, lifecycle supervision, health checks, and context building into focused modules.
- **Import model:** Programmatic extensions are loaded via `importlib.util.spec_from_file_location(...)` using `manifest.entrypoint` (for example `main:MyExtension`). In this mode extension modules are not loaded as package modules, so `from .x import ...` may fail without a fallback.
- **Hard dependency contracts:** `depends_on` is a hard contract. If a dependency fails to load, initialize, or start, all dependents are cascaded to ERROR and never run. `get_extension()` raises `RuntimeError` (never returns `None`) when a declared dependency is unavailable. See [ADR 021](adr/021-hard-dependency-contracts.md) and [extensions.md](extensions.md#dependency-order).

**Extension import rule (important):**

- Prefer extension-local imports that work in both contexts:
  - package context (relative import works)
  - file-spec context used by Loader (relative import may not have a parent package)
- For sibling modules in the same extension (for example `executors.py`), use a safe fallback import strategy (explicit file-based import) if relative import fails.
- If an extension fails to import, Loader skips it and dependents that declare it in `depends_on` are also cascaded to ERROR. Tools from failed extensions are excluded from Orchestrator capabilities.

### EventBus

- **Location:** `core/events/bus.py`
- **Role:** Durable pub/sub. Events persisted to SQLite before delivery; at-least-once semantics. Failed handlers are retried up to `max_retries`; then events are dead-lettered.
- **See:** [event_bus.md](event_bus.md)

### MessageRouter

- **Location:** `core/extensions/routing/router.py`
- **Role:** Routes user messages to the Orchestrator; delivers responses to channels. In-memory pub/sub for `user_message` and `agent_response`. ContextProvider middleware enriches prompts before agent invocation.
- **Internal split:** `core/extensions/routing/` isolates agent invocation, approval coordination, response delivery, event wiring, scheduler wiring, and built-in context providers.
- **Key details:** `AgentInvoker` serializes concurrent agent invocations; `ThreadManager` owns runtime and named thread pools; `notify_user()` enables proactive messages. **user.message idempotency:** When invoked from EventBus with `event_id`, the router records completion in `user_message_processing`; duplicate replays skip agent execution, memory hooks, and channel delivery.
- **Streaming:** If the channel implements `StreamingChannelProvider`, `handle_user_message()` uses `invoke_agent_streamed()` and calls the channel's stream lifecycle (`on_stream_start` → `on_stream_chunk` / `on_stream_status` → `on_stream_end`). Otherwise it uses `invoke_agent()` and `send_to_user()` as before. See [ADR 010](adr/010-streaming.md) and [channels.md](channels.md#streaming).

### Persistence Services

- **Location:** `core/extensions/persistence/`
- **Role:** Owns persistent thread and project metadata outside hot-path routing logic.
- **Key modules:** `ThreadManager` manages runtime `SQLiteThread` objects plus persisted thread rows; `ProjectService` coordinates project CRUD and thread binding; `schema.py` centralizes SQLite DDL; `models.py` defines typed `ThreadInfo` and `ProjectInfo`.
- **Thread metadata:** persisted rows include `title`, `channel_id`, timestamps, and archive state; extensions such as `thread_titler` keep auto-title policy outside core.
- **Context access:** `ExtensionContext` now exposes thread/project APIs directly (`list_threads`, `create_thread`, `list_projects`, `create_project`, etc.) instead of proxying them through `MessageRouter`. See [ADR 029](adr/029-refactor-core-extensions-boundaries.md).

### Core Tools

- **Location:** `core/tools/`
- **Role:** Built-in tools available to the Orchestrator and declarative agents via `CoreToolsProvider`.
- **Tools:** `file` (read/write files), `apply_patch_tool` (patch files), `request_restart` (trigger system restart), `list_channels` / `send_to_channel` (channel selection; see [channels.md](channels.md)). Web search is provided by the **`web_search` extension** ([ADR 013](adr/013-web-search.md)), not by core — add `web_search` to the Orchestrator's tool set via extension dependencies or capabilities.
- **Shell execution:** Provided by the `shell_exec` extension (`sandbox/extensions/shell_exec/`), not by core. Config: `containered`, `timeout_seconds`, `max_output_length`.

### Orchestrator

- **Location:** `core/agents/orchestrator.py`
- **Role:** Main AI agent. Created via `create_orchestrator_agent()` factory with:
  - **core tools** — from `CoreToolsProvider` (file, restart, channel tools)
  - **extension tools** — from ToolProvider extensions (`loader.get_all_tools()`)
  - **delegation tools** — `list_agents`, `delegate_task`, `create_agent`, `list_models`, `list_available_tools` (from `make_delegation_tools()`)
  - **capabilities_summary** — natural-language summary injected into the prompt template
  - **instructions** — resolved from `agents.orchestrator.instructions` in settings (supports Jinja2 templates)
  - **model** — resolved via `model_router.get_model("orchestrator")`

### AgentRegistry

- **Location:** `core/agents/registry.py`
- **Role:** Central registry of available agents. Populated by Loader from `AgentProvider` extensions at startup. Queried by delegation tools (`list_agents`, `delegate_task`). Tracks active invocations per agent.
- **Features:** register/unregister, invoke with busy tracking, TTL-based cleanup for dynamic agents, `on_unregister` callback for resource cleanup.
- **See:** [ADR 017](adr/017-agents-registry.md)

### ModelCatalog

- **Location:** `core/llm/catalog.py`
- **Role:** Maps model names to structured metadata (`cost_tier`, `capability_tier`, `strengths`, `context_window`). Enables cost-aware delegation by the Orchestrator. Built-in defaults for common models; user overrides via `settings.yaml` `models` section.
- **See:** [llm.md](llm.md#modelcatalog-costcapability-routing), [ADR 019](adr/019-cost-capability-routing.md)

### ModelRouter

- **Location:** `core/llm/router.py`
- **Role:** Resolves `agent_id` → cached Model instance via `config/settings.yaml`. Supports multiple providers:
  - `openai` / `openai_compatible` — OpenAI, LM Studio, OpenRouter, etc.
  - `openai_compatible` with `api_mode: chat_completions` — Chat Completions API for providers that don't support Responses.
  - `anthropic` — Claude models via Anthropic API.
- **Features:** per-agent model caching, `supports_hosted_tools()` check, provider health checks, dynamic agent config registration from extension manifests.
- **See:** [llm.md](llm.md)

---

## Data Flow: User Message → Response

```
Channel (cli_channel / telegram_channel / web_channel)
  │  ctx.emit("user.message", {text, user_id, channel_id})
  ▼
EventBus  (journal → dispatch)
  │  kernel_user_message_handler (wired in loader.wire_event_subscriptions)
  │  resolves channel_id → ChannelProvider
  ▼
router.handle_user_message(text, user_id, channel, event_id=event.id)
  ├─ if event_id and already in user_message_processing → skip (idempotency)
  ├─ if thread_id passed by channel → ThreadManager.get_or_create_thread(thread_id)
  ├─ else → inactivity-based default thread rotation
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
  │    │    └─ Runner.run(agent, prompt, session=SQLiteThread)
  │    └─ channel.send_to_user(user_id, response)
  ├─ _emit("agent_response")              → Memory.save_episode (subscriber)
  ├─ if event_id: record in user_message_processing (idempotency)
  └─ (response already delivered by channel)
```

See [event_bus-memory-flow.md](event_bus-memory-flow.md) for detailed flow and [ADR 010](adr/010-streaming.md) for streaming design.

---

## Extension Categories

| Category | Extensions | Protocols | Purpose |
|----------|-----------|-----------|---------|
| **Channels** | cli_channel, telegram_channel, web_channel | ChannelProvider, StreamingChannelProvider, ServiceProvider | Receive user input; deliver agent responses over terminal, Telegram, or HTTP/SSE |
| **Tools** | kv, scheduler, web_search, shell_exec, inbox | ToolProvider | Tools for Orchestrator (kv_set/kv_get, schedule_once, web_search/open_page, shell execution, inbox_list/inbox_read) |
| **Agents** | builder_agent, simple_agent | AgentProvider | Specialized agents invoked as tools (`integration_mode: tool`) |
| **Memory** | memory | ToolProvider, ContextProvider, SchedulerProvider | Graph-based cognitive memory: episodes, facts, procedures, opinions. Intent-aware hybrid retrieval, LLM-powered consolidation, Ebbinghaus decay |
| **Background work** | task_engine | ToolProvider, ServiceProvider | Durable multi-step task execution with checkpointing, retries, subtasks, and human review |
| **Infrastructure** | embedding | (internal API) | Embedding generation for memory and other extensions |

---

## References

- [extensions.md](extensions.md) — Extension architecture and manifest
- [event_bus.md](event_bus.md) — Event Bus
- [event_bus-memory-flow.md](event_bus-memory-flow.md) — Detailed message flow
- [memory.md](memory.md) — Memory system
- [llm.md](llm.md) — Model routing and ModelCatalog
- [channels.md](channels.md) — Channel extensions
- [scheduler.md](scheduler.md) — Scheduler extension
- [configuration.md](configuration.md) — Settings reference
- [ADR 017](adr/017-agents-registry.md) — Agent Registry and Dynamic Delegation
- [ADR 019](adr/019-cost-capability-routing.md) — Cost/Capability Routing
- [ADR 020](adr/020-consolidate-openai-compatible-provider.md) — Consolidate OpenAI-Compatible Provider
- [ADR 021](adr/021-hard-dependency-contracts.md) — Hard Dependency Contracts
- [ADR 024](adr/024-unified-inbox.md) — Unified Inbox Extension
- [ADR 026](adr/026-web-channel.md) — Web Channel HTTP API
- [ADR 029](adr/029-refactor-core-extensions-boundaries.md) — `core.extensions` Boundaries Refactor

