# assistant4 Documentation

Documentation for the assistant4 AI agent platform.

---

## Core Documentation

| Document | Description |
|----------|-------------|
| [architecture.md](architecture.md) | System overview, entry points, bootstrap flow, component diagram |
| [extensions.md](extensions.md) | Extension architecture, protocols, manifest, creating extensions |
| [event_bus.md](event_bus.md) | Event Bus: durable pub/sub, topics, API |
| [event_bus-memory-flow.md](event_bus-memory-flow.md) | Data flow: user message → memory → consolidation |

---

## Feature Documentation

| Document | Description |
|----------|-------------|
| [memory.md](memory.md) | Memory system: layers, extensions, database, search, tools |
| [heartbeat.md](heartbeat.md) | Agent loop: Scout → Orchestrator escalation |
| [channels.md](channels.md) | Channel providers: CLI, Telegram |
| [scheduler.md](scheduler.md) | Scheduler extension: one-shot and recurring events |
| [llm.md](llm.md) | Model routing, providers, configuration |
| [configuration.md](configuration.md) | Settings reference (config/settings.yaml) |

---

## Architecture Decision Records (ADR)

| ADR | Title |
|-----|-------|
| [001](adr/001-supervisor-agent-processes.md) | Supervisor and AI Agent as Separate Processes |
| [002](adr/002-extensions.md) | Extensions |
| [003](adr/003-agent-as-extension.md) | Agent as Extension |
| [004](adr/004-event-bus.md) | Event Bus |
| [005](adr/005-memory.md) | Simplified Memory System |
| [006](adr/006-mcp-extension.md) | MCP Extension |
| [007](adr/007-user-channel-selector.md) | Agent-Driven Channel Selection |

---

## Quick Links

- **Entry point:** `python -m supervisor`
- **Extensions:** `sandbox/extensions/<id>/`
- **Config:** `config/settings.yaml`
- **Core:** `core/` (runner, loader, events, llm, agents)
