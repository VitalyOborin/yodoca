# AGENTS.md — Yodoca Agent Engineering Protocol

This file provides **default working protocol and context** for AI coding agents (Cursor, Copilot, Codex, etc.) in this repository. It complements `.cursor/rules/` and `.cursor/skills/`; read it first for a project snapshot, then follow the referenced rules and skills for implementation details.

**Scope:** Entire repository. Keep instructions actionable and under ~400 lines; reference docs and skills instead of duplicating them.

---

## 1. Project snapshot (read first)

**Yodoca** is an autonomous AI agent runtime with:

- **All-is-extension** — All user-facing functionality (channels, memory, tools, schedulers, agents) lives in extensions under `sandbox/extensions/<id>/`. The core is a nano-kernel: Loader, EventBus, MessageRouter, ModelRouter, Orchestrator.
- **Core must not depend on extensions** — Core discovers and wires extensions by protocol only; no imports from `sandbox` in `core/`. Extensions depend only on contracts and `ExtensionContext`. See [.cursor/skills/core_boundary/SKILL.md](.cursor/skills/core_boundary/SKILL.md).
- **Event-driven** — Durable SQLite event journal; extensions publish/subscribe via `context.emit()` and `context.subscribe_event()`.
- **Stack** — Python 3.12+, uv for deps, OpenAI Agents SDK, Pydantic, SQLite (events, memory, tasks). Config: `config/settings.yaml`; secrets: keyring or `.env`.

**Entry points:** `uv run python -m supervisor` (production); `uv run python -m core` (agent process, usually spawned by supervisor). Onboarding: `uv run python -m onboarding` when config is missing.

**Key docs:** [docs/README.md](docs/README.md) (index), [docs/architecture.md](docs/architecture.md) (bootstrap, components), [docs/extensions.md](docs/extensions.md) (extension contract, manifest, context API).

---

## 2. Dev environment tips

- **Setup:** `uv sync` (or `uv sync --extra dev` for lint/test deps). Use `uv run <command>` so the correct venv is used.
- **Run the app:** `uv run python -m supervisor`. If config is missing, onboarding runs automatically; or run `uv run python -m onboarding` manually.
- **Config:** `config/settings.yaml` (create from `config/settings.example.yaml` if present). Never put API keys in YAML; use keyring or `.env`. Reset everything: `uv run python scripts/reset.py`.
- **Extensions:** Each extension is `sandbox/extensions/<id>/` with `manifest.yaml` and `main.py` (or declarative agent with `agent` section only). Add new extensions there; core does not reference extension IDs for behavior.
- **Prompts:** System agent prompts live in `prompts/` (root). Extension-specific prompts can live in `sandbox/extensions/<id>/` (e.g. `prompt.jinja2`). See [.cursor/rules/development.mdc](.cursor/rules/development.mdc).
- **Jump to code:** Core bootstrap: `core/runner.py`. Extension contracts: `core/extensions/contract.py`. Loader: `core/extensions/loader.py`. Example extensions: `sandbox/extensions/cli_channel/`, `sandbox/extensions/memory/`, `sandbox/extensions/task_engine/`.

---

## 3. Testing and quality checks

Install dev dependencies first: `uv sync --extra dev`.

| Check | Command | Notes |
|-------|---------|--------|
| **Lint** | `uv run ruff check .` | Fix auto-fixable: `uv run ruff check . --fix` |
| **Format** | `uv run ruff format .` | Check only: `uv run ruff format . --check` |
| **Import layers** | `uv run lint-imports` | Enforces: core must not import extensions (see pyproject `importlinter`) |
| **Types** | `uv run mypy` | Strict mode; overrides for tests/scripts |
| **Security** | `uv run bandit -r core onboarding sandbox` | Basic scan (B101 skipped) |
| **Tests** | `uv run pytest` | Asyncio mode auto; tests in `tests/` |

**Before committing / PR:** Run at least `ruff check .`, `ruff format --check .`, and `lint-imports`. Fix test and type errors; add or update tests for changed behavior. Do not commit with failing checks.

---

## 4. PR and commit expectations

- **Title:** Clear, concise. Prefer conventional style where it helps (e.g. `feat(ext): add X`, `docs: update Y`). No strict format required.
- **Checks:** All of §3 must pass. CI may run the same commands.
- **Architecture changes:** Must be documented in `docs/adr/` **before** implementation. Create `docs/adr/NNN-short-slug.md` (e.g. `016-new-feature.md`) with Status, Context, Decision, Consequences. See [.cursor/skills/adr/SKILL.md](.cursor/skills/adr/SKILL.md) and [.cursor/rules/adr.mdc](.cursor/rules/adr.mdc).
- **Documentation:** If you change behavior or contracts, update the relevant doc in `docs/` (and ADR if applicable). Required by project rules.

---

## 5. Coding conventions and rules

These align with `.cursor/rules/` and `.cursor/skills/`. When in doubt, follow the referenced rule or skill.

### Architecture

- **Core–extension boundary:** Core (`core/`) must not import or branch on concrete extensions. Extensions use only `ExtensionContext` and contracts (`core/extensions/contract.py`, `core/llm/capabilities.py`). [.cursor/skills/core_boundary/SKILL.md](.cursor/skills/core_boundary/SKILL.md)
- **Extensions:** One folder per extension; capabilities detected by `isinstance(ext, Protocol)`. Use `context` for all kernel interaction; no direct core internals. [.cursor/skills/extensions/SKILL.md](.cursor/skills/extensions/SKILL.md)
- **ADR:** One file per architectural decision in `docs/adr/NNN-slug.md`. Write ADR before implementing the change. [.cursor/skills/adr/SKILL.md](.cursor/skills/adr/SKILL.md)

### Agent tools (Orchestrator / ToolProvider)

- **Structured output only:** Every tool exposed to the agent MUST return a structured value: Pydantic model or JSON-serializable dict with consistent keys (e.g. `success`, `error`, `status`). Never return a bare string. [.cursor/skills/agent_tools/SKILL.md](.cursor/skills/agent_tools/SKILL.md)
- **Parameters:** Prefer typed parameters (or a Pydantic input model) over a single `str` for structured input. Avoid opaque JSON strings the LLM must emit.
- **Errors:** Prefer returning a result with `status="error: ..."` or `success: False` over raising; keep the same shape as success.

### General code

- **SOLID:** Apply Single Responsibility, Open/Closed, Interface Segregation, Dependency Inversion, Liskov Substitution. [.cursor/rules/solid.mdc](.cursor/rules/solid.mdc)
- **Time:** Use `datetime.now(timezone.utc)` instead of `datetime.utcnow()`.
- **Unicode:** Serialize UTF-8 “as is”, e.g. `json.dumps(result, ensure_ascii=False)`.

### Where things live

- **Documentation:** All required project docs in `docs/`; read before starting a task and update after implementation if behavior or contracts change.
- **Prompts:** Root-level agent prompts in `prompts/`; extension prompts in `sandbox/extensions/<id>/` as needed.

---

## 6. Skills quick reference

Use these when implementing or reviewing the corresponding areas:

| Skill | Path | Use when |
|-------|------|----------|
| Extensions | [.cursor/skills/extensions/SKILL.md](.cursor/skills/extensions/SKILL.md) | Creating/modifying extensions, manifest, context API, protocols |
| Core boundary | [.cursor/skills/core_boundary/SKILL.md](.cursor/skills/core_boundary/SKILL.md) | Adding code in core or extensions, reviewing imports |
| Agent tools | [.cursor/skills/agent_tools/SKILL.md](.cursor/skills/agent_tools/SKILL.md) | ToolProvider tools, structured return types, Pydantic results |
| ADR | [.cursor/skills/adr/SKILL.md](.cursor/skills/adr/SKILL.md) | Proposing or documenting architectural decisions |
| Event bus | [.cursor/skills/event_bus/SKILL.md](.cursor/skills/event_bus/SKILL.md) | emit, subscribe_event, proactive flows, system topics |
| Configuration | [.cursor/skills/configuration/SKILL.md](.cursor/skills/configuration/SKILL.md) | settings.yaml, get_config, extension config priority |
| Secrets | [.cursor/skills/secrets/SKILL.md](.cursor/skills/secrets/SKILL.md) | get_secret, keyring, .env, onboarding |
| Capabilities | [.cursor/skills/capabilities/SKILL.md](.cursor/skills/capabilities/SKILL.md) | Provider-agnostic LLM/embedding access from extensions |
| Architecture review | [.cursor/skills/architecture_review/SKILL.md](.cursor/skills/architecture_review/SKILL.md) | Implementation review, alignment with architecture |
| Refactoring | [.cursor/skills/refactoring/SKILL.md](.cursor/skills/refactoring/SKILL.md) | Safe refactors; [.cursor/skills/simplifier/SKILL.md](.cursor/skills/simplifier/SKILL.md) for simplification |

---

## 7. Common tasks (pointers)

- **Add a new extension:** Create `sandbox/extensions/<id>/` with `manifest.yaml` and `main.py`. Implement protocols (e.g. `ToolProvider`, `ChannelProvider`). Use only `ExtensionContext`; add `depends_on` and `context.get_extension()` for other extensions. See [docs/extensions.md](docs/extensions.md) and extensions skill.

- **Add or modify a channel (communication channel):** Implement `ChannelProvider`: `send_to_user(user_id, message)` (reactive reply) and `send_message(message)` (proactive to default recipient). In `start()` or `run_background()`, receive user input and emit `context.emit("user.message", {text, user_id, channel_id})` with `channel_id = context.extension_id`. Optionally implement `StreamingChannelProvider` (`on_stream_start`, `on_stream_chunk`, `on_stream_status`, `on_stream_end`) for token-by-token delivery. The kernel routes responses to the channel that sent the message. See [docs/channels.md](docs/channels.md) and examples: `sandbox/extensions/cli_channel/`, `sandbox/extensions/telegram_channel/`.

- **Add an agent tool:** Implement `get_tools()` returning `@function_tool`-decorated functions; return Pydantic models or fixed-shape dicts. See agent_tools skill and e.g. `sandbox/extensions/memory/tools.py`.

- **Emit or subscribe to events:** `context.emit(topic, payload)` and `context.subscribe_event(topic, handler)`. Manifest `events.subscribes` can wire `notify_user` or `invoke_agent`; for custom logic use `subscribe_event` in `initialize()`. See event_bus skill and [docs/event_bus.md](docs/event_bus.md).

- **Add scheduled (cron) behavior:** Implement `SchedulerProvider`: `execute_task(self, task_name: str) -> dict | None`. In manifest add `schedules: [{name, cron, task?}]`; the Loader calls `execute_task(entry.task_name)` on each tick. Return `{"text": "..."}` to notify the user, or `None`. For one-shot or recurring events from the Orchestrator, use the **scheduler** extension tools (`schedule_once`, `schedule_recurring`); the scheduler emits events when due. See [docs/scheduler.md](docs/scheduler.md) and `sandbox/extensions/scheduler/`.

- **Work with background tasks (Task Engine):** The Orchestrator gets `submit_task`, `get_task_status`, `list_active_tasks`, `cancel_task`, `request_human_review`, `respond_to_review` when the **task_engine** extension is in its tool set (or `uses_tools`). To add task-engine tools to another agent, add `task_engine` to that agent’s `uses_tools` and `depends_on`. Tasks run in a worker with checkpointing and retries; completion is signaled by the `<<TASK_COMPLETE>>` marker in agent output. See [docs/task_engine.md](docs/task_engine.md) and `sandbox/extensions/task_engine/`.

- **Change extension config or secrets:** Read config via `context.get_config(key, default)` (resolution: `settings.yaml` → `extensions.<id>.<key>` then manifest `config.<key>`). Secrets: `await context.get_secret(name)` (keyring then `.env`); never store API keys in YAML. Add required env/secret names to manifest `secrets` for onboarding. See configuration and secrets skills, [docs/configuration.md](docs/configuration.md), [docs/secrets.md](docs/secrets.md).

- **Update agent prompts:** System/orchestrator prompts live in `prompts/` (root); extension-specific prompts in `sandbox/extensions/<id>/` (e.g. `prompt.jinja2`). Loader merges extension prompt file + manifest `agent.instructions`. Use Jinja2 for templates. See [.cursor/rules/development.mdc](.cursor/rules/development.mdc).

- **Enrich agent context (ContextProvider):** Implement `ContextProvider`: `get_context(prompt, turn_context)` and `context_priority` (lower = earlier in chain). Return a string to inject into the system prompt, or `None`. Used e.g. by the memory extension for intent-aware retrieval. Wire is automatic via Loader. See [docs/extensions.md](docs/extensions.md) (ContextProvider) and `sandbox/extensions/memory/`.

- **Propose an architectural change:** Create an ADR in `docs/adr/NNN-slug.md` with Status, Context, Decision, Consequences; get alignment, then implement. See adr skill.

---

*This file is the single AGENTS.md at project root. Nested AGENTS.md in subprojects are not used currently. Update this file when project-wide protocol or conventions change; keep references to rules and skills in sync with `.cursor/`.*
