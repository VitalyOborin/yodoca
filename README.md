
<div align="center">
  <h1>Yodoca</h1>
  <p><strong>Self-Evolving AI Agent Platform with proactive memory and extensible architecture</strong></p>
  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" /></a>
    <img src="https://img.shields.io/badge/python-3.12+-blue.svg" />
    <img src="https://img.shields.io/badge/LLM-OpenAI%20%7C%20Anthropic%20%7C%20Local-green.svg" />
    <img src="https://img.shields.io/badge/storage-SQLite%20only-lightgrey.svg" />
  </p>
  <p>
    <a href="#quick-start">Quick Start</a> ·
    <a href="docs/">Docs</a> ·
    <a href="sandbox/extensions/">Extensions</a>
  </p>
</div>

---

> **Yodoca** is an event-driven AI agent that runs entirely on your machine.
> It remembers what matters, acts proactively between conversations,
> and can be extended with new tools, agents, and channels — all via a
> simple manifest file.

## Why Yodoca?

| Feature | What it means |
|---|---|
| 🧠 **Long-term memory** | Hybrid FTS5 + vector (sqlite-vec) + entity search. Survives restarts. Ebbinghaus decay built in. |
| 💓 **Heartbeat loop** | Scout → Orchestrator escalation every 2 min. Agent acts *between* conversations, not only when you write. |
| 🔌 **Extensions-only kernel** | Every feature — channels, memory, agents, schedulers — is an extension. Core has zero user-facing code. |
| 📦 **Declarative agents** | Define a sub-agent in one `manifest.yaml`. No Python required. |
| 🔄 **Multi-provider LLM** | OpenAI, Anthropic, LM Studio, OpenRouter — per-agent model routing from config. |
| 💾 **Zero external deps** | SQLite for events, memory, sessions. No Redis, no Postgres, no cloud. |
| 🛡 **Supervisor** | Auto-restart on crash, restart-by-file, backoff. Run `python -m supervisor` and forget. |

[License](LICENSE)

AI agent runtime designed for **always-on automation** and **self-extension**.  
The core stays tiny (nano-kernel); all capabilities live in **extensions** (channels, tools, services, schedulers, even other agents).  
A **Supervisor** manages lifecycle and safe restarts, while a **durable SQLite event journal** enables proactive flows and auditability.

> Status: Early / experimental. Single-user, self-hosted by design.

---

## Why this project

Most "AI assistants" are reactive: they respond only when you type. This project targets a different UX:

- **Push, not pull**: channels (Telegram, CLI, Web UI) wake the agent when messages arrive.
- **Proactive automation**: schedulers and background services emit events; the agent can react without manual prompts.
- **Self-evolving**: a Builder agent can generate new extensions and trigger a controlled restart to load them.

---

## Core ideas

### 1) Supervisor + Core as separate processes

You always run the app via the Supervisor:

- Supervisor is the **only entry point**.
- Core (nano-kernel + orchestrator) runs as a **child process**.
- Extensions can request a restart by writing a flag; Supervisor restarts core safely.

### 2) Nano-kernel

The kernel intentionally does very little:

- discovers and loads extensions
- wires them via a small `ExtensionContext`
- routes messages (reactive and proactive paths)
- runs a durable event dispatch loop (event-driven path)

### 3) Extensions-first architecture

All functionality lives in extensions under `sandbox/extensions/<extension_id>/`.

Extensions are "typed" by the protocols they implement (capabilities are detected at runtime):

- `ChannelProvider` — receive user messages and send responses (reactive + proactive); CLI, Telegram
- `ToolProvider` — expose tools/functions to the orchestrator
- `AgentProvider` — specialized agents invoked as tools (Builder, declarative agents)
- `ServiceProvider` — background loops (caches, inbox watchers, MCP servers)
- `SchedulerProvider` — cron-like tasks (reminders, periodic monitors)
- `SetupProvider` — guided configuration (tokens, secrets, credentials)

### 4) Durable Event Bus (SQLite journal)

A single SQLite table acts as:

- a queue (pending → processing → done/failed)
- an audit log of "what happened"
- a backbone for proactive workflows

Delivery is **at-least-once**, so handlers should be idempotent or deduplicate by `event.id`.

### 5) Memory as an extension

Long-term memory is implemented as an extension, with:

- fast append-only writes on the hot path
- background consolidation (summaries, indexing)
- optional full-text search (and embeddings later, if needed)

---

## Architecture (high level)

```
┌────────────────────────────────────────┐
│               SUPERVISOR               │  process watcher
│        spawn · monitor · restart       │
└────────────────────┬───────────────────┘
│ subprocess
┌────────────────────▼───────────────────┐
│               NANO-KERNEL              │
│ Loader → MessageRouter → Orchestrator  │
│        → EventBus (SQLite journal)     │
└───────────────┬───────────────┬────────┘
                │
  sandbox/extensions/      sandbox/data/
(channels/tools/etc.)   (per-extension state)
```

---

## Features

- Always-on runtime via Supervisor-managed core process
- Extension system with a minimal, generation-friendly contract (`manifest.yaml` + `main.py`)
- Channels: CLI + (optional) Telegram; agent can choose channel via `list_channels` / `send_to_channel` tools
- Durable event bus (SQLite WAL) for asynchronous flows + observability
- Scheduler extensions for cron-like automation (one-shot and recurring)
- Memory extension for long-term context (hybrid search, consolidation, decay)
- Heartbeat loop: Scout → Orchestrator escalation every 2 min
- Agent-as-extension: Builder agent, declarative agents (manifest-only)
- Safe restarts initiated by extensions (e.g., after generating new code)

---

## Quick start

### Prerequisites

- Python 3.12+
- uv
- Works best on a local machine (Windows/Linux/macOS)

### Run

```bash
# 1) Clone
git clone https://github.com/VitalyOborin/yodoca
cd yodoca

# 2) Create venv and install deps (uv recommended)
uv sync
# Or with pip:
# pip install -e .

# 3) Configure secrets (optional, for certain extensions)
cp .env.example .env
# edit .env — add OPENAI_API_KEY, etc.

# 4) Start via Supervisor
uv run python -m supervisor
```

---

## Configuration

LLM providers and models are configured in `config/settings.yaml`:

- `agents` — per-agent: `provider`, `model`, optional `instructions`, `temperature`, `max_tokens`
- `providers` — API definitions: `type` (openai_compatible / anthropic), `base_url`, `api_key_secret` or `api_key_literal`

Secrets live in `.env`; never store API keys in YAML. See [docs/configuration.md](docs/configuration.md) and `config/settings.yaml` for examples.

---

## Repository layout (conceptual)

```
supervisor/              # process watcher (spawn, monitor, restart)
  __main__.py
  runner.py

core/                    # nano-kernel
  __main__.py
  runner.py              # bootstrap: Loader, EventBus, MessageRouter, Orchestrator
  agents/                # orchestrator, agent factory
  extensions/            # contracts, loader, context, router
  events/                # SQLite journal-backed EventBus
  tools/                 # core tools (file, channel, restart, etc.)
  llm/                   # ModelRouter, providers

sandbox/
  extensions/            # all extensions live here
    cli_channel/         # stdin/stdout REPL
    telegram_channel/     # Telegram Bot API (aiogram)
    memory/              # long-term episodic + semantic memory
    scheduler/           # one-shot and recurring events
    heartbeat/           # Scout → Orchestrator escalation
    kv/                  # key-value store (secrets, config)
    builder_agent/        # code-generation agent
    ...
  data/                  # per-extension private data (SQLite, caches, etc.)
```

---

## Creating an extension

Each extension is a folder:

```
sandbox/extensions/<extension_id>/
  manifest.yaml
  main.py
```

### `manifest.yaml` (minimal example)

```yaml
id: telegram_channel
name: Telegram Bot Channel
version: "1.0.0"

description: >
  User communication channel via Telegram bot.
  Receives incoming messages and sends agent responses.

entrypoint: main:TelegramChannelExtension

setup_instructions: |
  A bot token from @BotFather is needed for setup.

depends_on:
  - kv

config:
  parse_mode: MarkdownV2

enabled: true
```

### Capabilities (by protocol)

Implement one or more protocols in `main.py`:

- Channel (receive/send)
- Tool (functions for the agent)
- Service (background loop)
- Scheduler (cron task)
- Setup (configuration flow)

---

## Event-driven flows

Extensions can publish events (durable) and subscribe to topics.

Examples:

- `email.received` → a processor extension formats a prompt → invokes the agent → emits `user.notify`
- `tick` → monitoring extension checks something → emits `user.notify`

The event journal provides traceability and debugging:

- pending/processing/done/failed
- correlation IDs for causal chains (optional)

---

## Security notes

This is a **single-user local** system.

- Extensions are trusted code running on your machine.
- Secrets should be stored in `.env` (or OS keychain later).
- If you plan to run untrusted extensions, add sandboxing/isolation (future work).

---

## Roadmap (suggested)

- Better extension packaging/versioning + compatibility checks
- Optional WASM sandboxing for untrusted extensions
- Web UI channel
- Event retries / dead-letter support
- Memory: embeddings + retrieval policies (opt-in)
- MCP extension: bridge to Model Context Protocol servers (web search, filesystem, etc.)

---

## Documentation

Detailed docs live in [`docs/`](docs/):

- [Architecture](docs/architecture.md) — bootstrap flow, components, protocols
- [Extensions](docs/extensions.md) — manifest, protocols, creating extensions
- [Channels](docs/channels.md) — CLI, Telegram, agent channel tools
- [Memory](docs/memory.md), [Heartbeat](docs/heartbeat.md), [Scheduler](docs/scheduler.md)
- [ADRs](docs/README.md#architecture-decision-records-adr) — architecture decisions

---

## Contributing

Contributions are welcome:

- new extensions (channels, tools, integrations)
- docs and examples
- bug reports and reproducible test cases
- architecture proposals as ADRs (see `docs/adr/`)

Open a PR and include:

- motivation/use case
- a minimal working example
- tests (when applicable)

---

## Acknowledgements

Inspired by practical lessons from building local-first agent runtimes:

- keep the core tiny
- move capabilities to extensions
- make background work observable and durable
- optimize for iteration and real-world workflows

