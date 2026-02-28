
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
    <a href="#onboarding">Onboarding</a> ·
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
| 🧠 **Long-term memory** | Graph-based (nodes, edges, entities). Hybrid FTS5 + vector (sqlite-vec) search. Ebbinghaus decay, nightly consolidation. |
| 🔌 **Extensions-only kernel** | Every feature — channels, memory, agents, schedulers — is an extension. Core has zero user-facing code. |
| 📦 **Declarative agents** | Define a sub-agent in one `manifest.yaml`. No Python required. |
| 🔄 **Multi-provider LLM** | OpenAI, Anthropic, LM Studio, OpenRouter — per-agent model routing from config. |
| 💾 **Zero external deps** | SQLite for events, memory, sessions. No Redis, no Postgres, no cloud. |
| 🛡 **Supervisor** | Auto-restart on crash, restart-by-file, backoff. Run `python -m supervisor` and forget. |
| 🔐 **Secrets** | API keys in OS keyring (Windows Credential Manager, Keychain) or `.env` fallback. |
| 🧭 **Onboarding** | Guided setup wizard when config is missing. Supervisor launches it automatically. |

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
- `StreamingChannelProvider` — incremental response delivery (token-by-token streaming)
- `ToolProvider` — expose tools/functions to the orchestrator
- `AgentProvider` — specialized agents invoked as tools (Builder, declarative agents)
- `ServiceProvider` — background loops (caches, inbox watchers, MCP servers)
- `SchedulerProvider` — cron-like tasks (reminders, periodic monitors)
- `ContextProvider` — enrich agent prompt with relevant context before each invocation (e.g. memory retrieval)
- `SetupProvider` — guided configuration (tokens, secrets, credentials)

### 4) Durable Event Bus (SQLite journal)

A single SQLite table acts as:

- a queue (pending → processing → done/failed)
- an audit log of "what happened"
- a backbone for proactive workflows

Delivery is **at-least-once**, so handlers should be idempotent or deduplicate by `event.id`.

### 5) Memory as an extension

Long-term memory is a graph-based extension (`memory` + `embedding`):

- typed graph schema: nodes (fact / episode / procedure / opinion), edges, entities
- hybrid retrieval: FTS5 + vector (sqlite-vec) + graph traversal, fused via RRF
- LLM-powered write path: consolidation, entity enrichment, causal inference
- Ebbinghaus decay with access reinforcement; nightly maintenance pipeline

---

## Architecture (high level)

```
┌────────────────────────────────────────┐
│               SUPERVISOR               │  process watcher
│  config check → onboarding if needed   │
│        spawn · monitor · restart       │
└────────────────────┬───────────────────┘
                     │ subprocess
┌────────────────────▼───────────────────┐
│               NANO-KERNEL              │
│ Loader → MessageRouter → Orchestrator  │
│        → EventBus (SQLite journal)     │
└──────────┬────────────────────┬────────┘
           │                    │
  sandbox/extensions/      sandbox/data/
 (channels/tools/etc.)  (per-extension state)
```

---

## Features

- Always-on runtime via Supervisor-managed core process
- **Onboarding wizard** — guided setup when config is missing; Supervisor launches it automatically
- **Secrets** — API keys in OS keyring (Windows Credential Manager, macOS Keychain) or `.env` fallback
- Extension system with a minimal, generation-friendly contract (`manifest.yaml` + `main.py`)
- Channels: CLI + (optional) Telegram; agent can choose channel via `list_channels` / `send_to_channel` tools
- Durable event bus (SQLite WAL) for asynchronous flows + observability
- Scheduler extensions for cron-like automation (one-shot and recurring)
- **Memory v2** — graph-based cognitive memory (nodes, edges, entities), hybrid FTS5 + vector search, Ebbinghaus decay, nightly consolidation
- **Embedding extension** — separate provider for memory vector search (OpenAI, OpenRouter, local)
- **Task Engine** — durable multi-step background tasks with checkpointing, retries, subtasks, and human review
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

# 3) Start via Supervisor — onboarding runs automatically if config is missing
uv run python -m supervisor
```

On first run, if `config/settings.yaml` is absent or incomplete, the Supervisor launches the **onboarding wizard** interactively. You can also run it manually: `uv run python -m onboarding`.

---

## Onboarding

When you run `python -m supervisor`, it first checks whether the app is configured (`config/settings.yaml` exists, providers have API keys, etc.). If not, it starts the **onboarding wizard** (`python -m onboarding`):

1. **Provider step** — Select LLM providers (OpenAI, Anthropic, OpenRouter, LM Studio), enter API keys, choose models.
2. **Embedding step** — Memory needs an embedding model; choose one (or reuse the default provider if it supports embeddings).
3. **Verify step** — Probes each provider to confirm connectivity; you can retry or cancel.

On success, the wizard writes `config/settings.yaml` and stores secrets in the OS keyring (when available) or `.env`. See [docs/secrets.md](docs/secrets.md) for details.

---

## Configuration

LLM providers and models are configured in `config/settings.yaml`:

- `agents` — per-agent: `provider`, `model`, optional `instructions`, `temperature`, `max_tokens`
- `providers` — API definitions: `type` (openai_compatible / anthropic), `base_url`, `api_key_secret` or `api_key_literal`

Secrets live in the OS keyring (when available) or `.env`; never store API keys in YAML. See [docs/configuration.md](docs/configuration.md), [docs/secrets.md](docs/secrets.md), and `config/settings.yaml` for examples.

To reset everything (config, memory, secrets) and start fresh: `uv run python scripts/reset.py`.

---

## Development checks

Install dev dependencies and run linting, formatting, and architecture checks:

```bash
uv sync --extra dev
```

| Check | Command | Description |
|-------|---------|-------------|
| **Lint** | `uv run ruff check .` | Style and bug checks (fix with `--fix`) |
| **Format** | `uv run ruff format .` | Format code (Black-compatible) |
| **Import layers** | `uv run lint-imports` | Enforce architecture (core must not import extensions) |
| **Types** | `uv run mypy` | Static typing (strict mode) |
| **Security** | `uv run bandit -r core onboarding sandbox` | Basic security scan |
| **Tests** | `uv run pytest` | Run test suite |

Before opening a PR, run at least: `ruff check .`, `ruff format --check .`, and `lint-imports`.

---

## Repository layout (conceptual)

```
supervisor/              # process watcher (spawn, monitor, restart)
  __main__.py
  runner.py

onboarding/              # setup wizard (providers, embedding, verify)
  __main__.py
  wizard.py
  config_writer.py
  steps/

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
    telegram_channel/    # Telegram Bot API (aiogram)
    memory/              # graph-based long-term memory (FTS5 + vector + entities)
    embedding/           # embedding generation for memory search
    scheduler/           # one-shot and recurring events
    task_engine/         # durable background tasks with retries
    kv/                  # key-value store (secrets, config)
    builder_agent/       # code-generation agent
    simple_agent/        # declarative sub-agent (manifest-only)
    ...
  data/                  # per-extension private data (SQLite, caches, etc.)

scripts/                 # utilities
  reset.py               # wipe config, memory, secrets (fresh start)
  run_memory_maintenance.py  # manual consolidation/decay run
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
name: Telegram Channel
version: "0.2.0"
description: |
  Telegram Bot API channel via aiogram long-polling.
  Setup: save secret 'telegram_channel_token' (Bot API token from @BotFather),
  then call request_restart().
entrypoint: main:TelegramChannelExtension
depends_on:
  - kv
config:
  token_secret: telegram_channel_token
  polling_timeout: 10
  streaming_enabled: true
enabled: true
```

### Capabilities (by protocol)

Implement one or more protocols in `main.py`:

- Channel (receive/send messages)
- StreamingChannel (incremental response delivery)
- Tool (functions for the agent)
- Agent (specialized sub-agent invoked as a tool)
- Service (background loop)
- Scheduler (cron task)
- Context (enrich agent prompt before each turn)
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
- Secrets are stored in the OS keyring (when available) or `.env`; see [docs/secrets.md](docs/secrets.md).
- If you plan to run untrusted extensions, add sandboxing/isolation (future work).

---

## Roadmap (suggested)

- Better extension packaging/versioning + compatibility checks
- Optional WASM sandboxing for untrusted extensions
- Web UI channel
- Event retries / dead-letter support
- MCP extension: bridge to Model Context Protocol servers (web search, filesystem, etc.) — [ADR 006](docs/adr/006-mcp-extension.md)

---

## Documentation

Detailed docs live in [`docs/`](docs/):

- [Architecture](docs/architecture.md) — bootstrap flow, components, protocols
- [Extensions](docs/extensions.md) — manifest, protocols, creating extensions
- [Channels](docs/channels.md) — CLI, Telegram, agent channel tools
- [Memory](docs/memory.md), [Scheduler](docs/scheduler.md), [Task Engine](docs/task_engine.md)
- [Secrets](docs/secrets.md) — keyring vs `.env`, onboarding flow
- [ADRs](docs/adr/) — architecture decisions

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
- passing [development checks](#development-checks): `ruff check .`, `ruff format --check .`, `lint-imports`

---

## Acknowledgements

Inspired by practical lessons from building local-first agent runtimes:

- keep the core tiny
- move capabilities to extensions
- make background work observable and durable
- optimize for iteration and real-world workflows

