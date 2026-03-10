# Yodoca Documentation

Documentation for the Yodoca AI agent platform (assistant4).

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
| [channels.md](channels.md) | Channel providers: CLI, Telegram |
| [api/web-channel-openapi.yaml](api/web-channel-openapi.yaml) | OpenAPI spec for Web Channel (ADR 026) |
| [scheduler.md](scheduler.md) | Scheduler extension: one-shot and recurring events |
| [task_engine.md](task_engine.md) | Task Engine: durable background tasks, checkpointing, HITL |
| [llm.md](llm.md) | Model routing, providers, configuration |
| [configuration.md](configuration.md) | Settings reference (config/settings.yaml) |
| [config.md](config.md) | Application config: file location, structure, extension config priority |

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
| [008](adr/008-memory-v2.md) | Memory v2: Graph-Based Cognitive Memory |
| [009](adr/009-memory-timestamp-output-format.md) | Memory Timestamp Output Format |
| [010](adr/010-streaming.md) | Streaming Response Delivery |
| [011](adr/011-onboarding.md) | Onboarding |
| [012](adr/012-secrets.md) | Secrets Management |
| [013](adr/013-web-search.md) | Web Search Extension |
| [014](adr/014-agent-loop2.md) | Task Engine and Agent Loop |
| [015](adr/015-skills.md) | Agent Skills System |
| [017](adr/017-agents-registry.md) | Agent Registry and Dynamic Delegation |
| [018](adr/018-task-chains.md) | Task Chains |
| [019](adr/019-cost-capability-routing.md) | Cost/Capability Routing |
| [020](adr/020-consolidate-openai-compatible-provider.md) | Consolidate OpenAI-Compatible Provider |
| [021](adr/021-hard-dependency-contracts.md) | Hard Dependency Contracts |
| [022](adr/022-move-prompts-to-sandbox.md) | Move Prompts Directory to Sandbox |
| [024](adr/024-unified-inbox.md) | Unified Inbox Extension |
| [025](adr/025-mail-extension.md) | Mail Extension (Source Extension for Email Ingestion) |
| [027](adr/027-session-project-domain-model.md) | Session and Project Domain Model in `session.db` |

---

## Quick Links

- **Entry point:** `python -m supervisor`
- **Extensions:** `sandbox/extensions/<id>/`
- **Config:** `config/settings.yaml`
- **Core:** `core/` (runner, loader, events, llm, agents)
