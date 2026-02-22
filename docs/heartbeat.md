# Heartbeat (Agent Loop)

The **Heartbeat** extension is the engine of the **agent loop**: it periodically wakes the system to check for proactive work, using a lightweight Scout agent and escalating to the Orchestrator only when needed. This document describes the architecture, interactions, limitations, and extension possibilities for developers.

**Principle:** The agent loop keeps the system "alive" — it reacts not only to user messages but also runs background checks on a schedule. Heartbeat uses the **Scout → Orchestrator escalation** pattern: a cheap model makes the initial decision; the full Orchestrator is invoked only when the task exceeds Scout capabilities.

---

## Overview

Heartbeat provides:

- **Periodic proactive checks** — Every 2 minutes (configurable via cron), the Scout agent reviews memory context and decides whether there is actionable work
- **Memory-aware context** — The Scout receives enriched prompts with relevant facts and weekly insights from the `memory` extension via `ContextProvider` middleware
- **Cost-efficient design** — Scout uses a lightweight model (e.g. `gpt-5-mini`); the Orchestrator (e.g. `gpt-5.2`) is invoked only on escalation
- **Structured output** — Scout returns a `HeartbeatDecision` (Pydantic) with `action` and `reason`; no free-form text parsing

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         Loader._cron_loop (every 60s)                           │
│  Evaluates schedules; when cron matches → execute_task("emit_heartbeat")        │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         HeartbeatExtension.execute_task                         │
│  1. base_prompt = config.prompt                                                 │
│  2. enriched = ctx.enrich_prompt(base_prompt, agent_id="heartbeat_scout")       │
│     └── ContextProvider chain (memory.get_context) → hybrid_search + reflection │
│  3. result = Runner.run(scout, enriched, max_turns=1)                           │
│  4. decision = result.final_output (HeartbeatDecision)                          │
│  5. match decision.action: noop | done | escalate                               │
└─────────────────────────────────────────────────────────────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
    logger.debug         logger.info         ctx.request_agent_task(reason)
    (no-op)              (done: reason)       → EventBus AGENT_TASK
                                              → Orchestrator → notify_user
```

---

## Data Flow

### 1. Schedule trigger

The Loader's `_cron_loop` runs every **60 seconds**. For each `SchedulerProvider` with a `schedules` entry, it checks whether `now >= next_run`. When the cron expression matches (e.g. `*/2 * * * *` every 2 minutes), it calls `heartbeat.execute_task("emit_heartbeat")`.

### 2. Prompt enrichment

Heartbeat does **not** call `router.invoke_agent()` directly. Instead, it uses `ctx.enrich_prompt(prompt, agent_id="heartbeat_scout")`, which applies the same `ContextProvider` middleware chain used by `invoke_agent` — but **without** invoking the Orchestrator. This ensures:

- The `memory` extension's `get_context()` is called
- Hybrid search over facts + weekly reflection are prepended to the prompt
- The Scout receives memory context even though it runs outside the main agent path

**Key API:** `enrich_prompt` is a public method on `MessageRouter` and `ExtensionContext`. Any extension can enrich a prompt with memory/context before passing it to its own agent.

### 3. Scout invocation

The Scout is an `Agent` built at initialization:

- **Model:** `heartbeat_scout` from `agent_config` in manifest (e.g. `gpt-5-mini`)
- **Instructions:** From `prompt.jinja2` in the extension directory
- **Tools:** Resolved from manifest `agent.uses_tools` — currently `memory` and `kv` (ToolProvider extensions). Tools from `memory_maintenance` and `memory_reflection` are listed in `uses_tools` but not included since they are not ToolProviders.
- **Output:** `HeartbeatDecision` (Pydantic) with `action` and `reason`

`Runner.run(scout, enriched, max_turns=1)` — single turn with structured output. The Scout can technically use tools (e.g. `search_memory`, `kv_get`) within its single turn, but the `output_type=HeartbeatDecision` constraint and `max_turns=1` mean it typically produces a decision directly from the enriched prompt.

### 4. Decision dispatch

| Action   | Behaviour                                                                 |
| -------- | ------------------------------------------------------------------------- |
| `noop`   | Log at debug level. No further action.                                    |
| `done`   | Log at info level with `reason`. Scout handled something simple.          |
| `escalate` | Call `ctx.request_agent_task(decision.reason)`. Emits `system.agent.task` → Orchestrator runs → response to user. |

---

## Interactions

### With Loader

- **Protocol:** `SchedulerProvider` — Heartbeat implements `execute_task(task_name)`.
- **Registration:** Loader detects `SchedulerProvider` in `detect_and_wire_all` and adds Heartbeat to `_schedulers`.
- **Schedule wiring:** Loader reads `schedules` from manifest and initialises `_task_next` for each `{ext_id}::{task_name}`. Cron evaluation happens in `_cron_loop`.
- **agent_config:** The manifest's `agent_config.heartbeat_scout` is registered in `ModelRouter` during `initialize_all`, so `get_model("heartbeat_scout")` resolves correctly.

### With Memory extension

- **ContextProvider:** The `memory` extension implements `ContextProvider`. Its `get_context(prompt, agent_id)` is invoked by the middleware chain when `enrich_prompt` runs.
- **Hybrid search:** Memory runs `hybrid_search(prompt, kind="fact", limit=5)` — the prompt text affects which facts are retrieved. A generic prompt like "Check if anything to do" may match few facts; a more specific prompt (e.g. "pending user requests, unfinished tasks, reminders") improves recall.
- **Weekly insight:** `_reflection_cache` (latest reflection) is always appended when available; it does not depend on the prompt.

### With Event Bus

- **Escalation path:** `ctx.request_agent_task(reason)` emits `system.agent.task` with `{prompt: reason, channel_id: null}`. The kernel handler invokes the Orchestrator and sends the response to the user via the default channel.
- **No direct EventBus use:** Heartbeat uses only `ExtensionContext` APIs; it does not import EventBus or publish events directly (except through `request_agent_task`).

### With Orchestrator

- **Indirect:** Heartbeat never calls the Orchestrator directly. Escalation goes through `AGENT_TASK` → kernel handler → `router.invoke_agent()`.
- **Serialization:** `MessageRouter.invoke_agent` uses an `asyncio.Lock`; concurrent user messages and escalated tasks are serialized.

---

## Limitations

### 1. Scout tools are limited by max_turns=1

The Scout agent receives tools from `memory` and `kv` via `context.resolved_tools` (resolved from manifest `agent.uses_tools`). However, with `max_turns=1` and `output_type=HeartbeatDecision`, the Scout typically produces a structured decision directly rather than making tool calls. If tool use is needed, the single turn limits it to one round of calls before the final output.

### 2. Memory context quality depends on prompt

`memory.get_context()` uses hybrid search over the prompt. A vague prompt yields fewer relevant facts. The default `config.prompt` uses concrete nouns ("pending user requests, unfinished tasks, reminders, follow-ups") to improve semantic matching.

### 3. Cron resolution is 60 seconds

The Loader's `_cron_loop` sleeps 60 seconds between evaluations. A schedule with `*/1 * * * *` (every minute) can fire up to ~60 seconds late. For sub-minute precision, a `ServiceProvider` with its own loop would be needed.

### 4. Single schedule per task name

The manifest supports multiple `schedules` entries, but each must have a unique `task` (or `task_name`). Heartbeat currently defines only `emit_heartbeat`. Adding a second schedule (e.g. `deep_think` every 6 hours) would require implementing `execute_task("deep_think")` in the extension.

### 5. No session for Scout

The Scout runs without `session` — it does not accumulate conversation history. Each invocation is stateless. Context comes only from `enrich_prompt` (memory facts + reflection).

---

## Configuration

### Manifest (`sandbox/extensions/heartbeat/manifest.yaml`)

| Section       | Key              | Description                                                                 |
| ------------- | ---------------- | --------------------------------------------------------------------------- |
| `depends_on`  |                  | `memory`, `memory_maintenance`, `memory_reflection`, `kv`                   |
| `agent`       | `uses_tools`     | `memory`, `memory_maintenance`, `memory_reflection`, `kv` — resolved to `context.resolved_tools` |
| `agent_config` | `heartbeat_scout` | Model config for Scout: `provider`, `model`. Registered in ModelRouter.   |
| `config`      | `prompt`         | Base prompt for the Scout. Should be memory-friendly (concrete nouns).      |
| `schedules`   | `agent_loop`     | Cron and task: `cron: "*/2 * * * *"`, `task: emit_heartbeat`.               |

### Scout instructions

Stored in `prompt.jinja2`. Loaded via `resolve_instructions(instructions_file="prompt.jinja2", ...)`. Defines the Scout's behaviour (noop / done / escalate) and the structured output contract.

---

## Extension Possibilities

### 1. Additional schedules

Add a second schedule (e.g. `deep_think` every 6 hours) and handle it in `execute_task`:

```python
async def execute_task(self, task_name: str) -> dict[str, Any] | None:
    if task_name == "emit_heartbeat":
        return await self._run_heartbeat()
    if task_name == "deep_think":
        return await self._run_deep_think()
    return None
```

### 2. Idle watcher

A separate extension could implement `ServiceProvider` and subscribe to `user_message` to track last activity. When idle for N minutes, it could emit `system.agent.background` or call a custom topic. This would complement the cron-based Heartbeat.

### 3. Expanding Scout tool access

The Scout already has `memory` and `kv` tools. Adding more (e.g. `scheduler` for `list_schedules`) requires adding the extension to both `depends_on` and `agent.uses_tools` in the manifest. Increasing `max_turns` would allow multi-step reasoning with tool calls, but increases cost and latency per heartbeat.

### 4. Custom ContextProvider for Heartbeat

A dedicated `ContextProvider` with `context_priority` lower than memory could inject Heartbeat-specific context (e.g. due schedules, inbox count) before the Scout runs. The `agent_id="heartbeat_scout"` parameter allows providers to specialise by agent.

### 5. Different models per schedule

The manifest's `agent_config` supports a single `heartbeat_scout`. To use different models for different tasks, you could extend the config (e.g. `heartbeat_deep_think`) and resolve the model in `execute_task` based on `task_name`.

---

## Files

| File                | Purpose                                                                 |
| ------------------- | ----------------------------------------------------------------------- |
| `main.py`           | `HeartbeatExtension` — lifecycle, Scout agent, `execute_task`, dispatch |
| `manifest.yaml`     | Extension metadata, `agent_config`, `config`, `schedules`               |
| `prompt.jinja2`     | Scout instructions (noop / done / escalate)                             |

---

## Observability

- **Logs:** `heartbeat: noop` (debug), `heartbeat: done — <reason>` (info), `heartbeat: escalate → <reason>` (info)
- **Errors:** `heartbeat scout failed: <e>` (warning) — Scout exceptions are caught; the extension does not crash
- **Escalation:** Escalated tasks emit `system.agent.task` and are handled by the kernel's `on_agent_task` handler (invoke Orchestrator, notify user). The `agent loop: start` / `agent loop: done` logs apply only to `system.agent.background` events, not to escalated `AGENT_TASK` flows.

---

## Design Decisions
 
| Decision                    | Rationale                                                                    |
| --------------------------- | -----------------------------------------------------------------------------|
| SchedulerProvider only      | Uses Core Cron Loop; no ServiceProvider, no duplicate scheduling logic       |
| Scout without AgentProvider | Scout is internal; Orchestrator does not need it as a tool. Tools from `uses_tools` are passed via `resolved_tools`. |
| Structured output           | Pydantic `HeartbeatDecision` avoids brittle text parsing                     |
| enrich_prompt in core       | Public API allows any extension to get memory context without invoking agent |
| agent_config in manifest    | Scout model lives with the extension; no global settings coupling            |
