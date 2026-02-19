# ADR 003: Agent-as-Extension

## Status

Proposed.

## Context

The current architecture (ADR 002) defines a nano-kernel with one Orchestrator agent in core and four extension protocols: `ToolProvider`, `ChannelProvider`, `ServiceProvider`, `SchedulerProvider`. All functionality lives in extensions; the kernel only loads extensions and wires them to the agent.

This works well for a single-agent system, but creates a bottleneck as the number of tools grows: every tool is registered on the Orchestrator, inflating its system prompt and increasing latency/cost per request. The Orchestrator must "know about" every tool, even when a task is domain-specific and needs only a narrow subset.

Additionally, `core/agents/builder.py` lives in the kernel, violating the "all functionality in extensions" principle. The Builder Agent is functionally an extension with a specific tool set and instructions, yet it has a privileged position in core.

Industry patterns confirm the direction: OpenAI Agents SDK formalizes Handoff and Agent-as-Tool as first-class primitives; Microsoft Multi-Agent Reference Architecture describes Dynamic Agent Registry and supervisor patterns; Anthropic recommends starting with simple workflows and escalating to agents only when needed. All converge on the same idea: specialized sub-agents with bounded tool sets, orchestrated by a central coordinator.

### Problems solved

1. **Tool explosion** — Orchestrator's context grows linearly with every new tool extension. Sub-agents encapsulate their own tool subsets.
2. **Kernel purity** — Builder Agent moves out of core, proving the architecture can bootstrap itself.
3. **Cost optimization** — cheap/fast models for routine sub-agents, expensive model only for the Orchestrator.
4. **Specialization** — domain-specific instructions and memory per agent, instead of one monolithic system prompt.

## Decision

### 1. New Protocol: `AgentProvider`

A new `@runtime_checkable` protocol is added to `contract.py`. An extension that implements `AgentProvider` provides a specialized AI agent to the system.

```python
@runtime_checkable
class AgentProvider(Protocol):
    """Extension that provides a specialized AI agent."""

    def get_agent_descriptor(self) -> "AgentDescriptor":
        """Return metadata: name, description for LLM routing, integration mode."""

    async def invoke(self, task: str, context: AgentInvocationContext | None = None) -> AgentResponse:
        """Execute a task and return a structured result."""
```

#### `AgentResponse` — structured return type

The `invoke()` method returns `AgentResponse`, not a raw string. This gives the Orchestrator structured information about what happened — success/failure, token usage, the ability to retry or escalate.

```python
@dataclass(frozen=True)
class AgentResponse:
    status: Literal["success", "error", "refused"]
    content: str
    error: str | None = None
    tokens_used: int | None = None
    turns_used: int | None = None
```

| Field | Description |
|-------|-------------|
| `status` | `success` — task completed; `error` — agent failed (retriable); `refused` — agent cannot handle this task |
| `content` | The agent's output text (main result for the Orchestrator) |
| `error` | Error description when `status != "success"` |
| `tokens_used` | Total tokens consumed (input + output), for budget tracking |
| `turns_used` | Number of agent loop iterations, for limit enforcement |

The Orchestrator uses `status` to decide: show `content` to the user, retry, fall back to another agent, or report the error. The `tokens_used` and `turns_used` fields feed into observability and budget tracking without requiring a separate tracing system in MVP.

#### `AgentInvocationContext` — typed invocation context

The second argument to `invoke()` is a typed dataclass, not an open `dict`. This prevents each extension from guessing what to expect and gives the Orchestrator a clear contract for what it provides.

```python
@dataclass(frozen=True)
class AgentInvocationContext:
    conversation_summary: str | None = None
    user_message: str | None = None
    correlation_id: str | None = None
```

| Field | Description |
|-------|-------------|
| `conversation_summary` | A condensed summary of relevant conversation history (not the full log) |
| `user_message` | The original user message that triggered this invocation |
| `correlation_id` | Trace ID for linking Orchestrator → sub-agent → tool call chains |

The context is intentionally narrow. The Orchestrator decides what to include — typically a short summary, not the full conversation. This enforces context isolation by design: sub-agents never see the raw conversation buffer.

#### `AgentDescriptor`

```python
@dataclass(frozen=True)
class AgentDescriptor:
    name: str
    description: str
    integration_mode: Literal["tool", "handoff"]
```

The `description` field is optimized for LLM routing (see Manifest section below). `integration_mode` determines how the Loader wires the agent.

#### Design rationale

The protocol is intentionally minimal — only `get_agent_descriptor()` and `invoke()`. The agent extension internally creates its own `Agent` instance (from `agents` SDK or any other library), with its own model, instructions, and tools. The kernel never touches these internals.

### 2. Integration Modes

| Mode | Behavior | Use case |
|------|----------|----------|
| **tool** | Orchestrator calls `invoke(task, context)` and receives `AgentResponse`. The sub-agent runs to completion and returns. Orchestrator stays in control. | Domain-specific subtasks: code review, email triage, data analysis |
| **handoff** | Orchestrator delegates the full conversation turn to the sub-agent. The sub-agent interacts with the user through the Orchestrator (proxied, not direct). Orchestrator monitors and can reclaim control. | Complex multi-turn workflows: tech support, travel booking, guided setup |

**`tool` mode is the default and primary mode.** It maps directly to the OpenAI Agent-as-Tool pattern: the Orchestrator wraps the sub-agent as a function tool and calls it like any other tool. The sub-agent receives only the task description and optional context — not the full conversation history.

**`handoff` mode** is reserved for scenarios where the sub-agent needs multi-turn interaction with the user. The Orchestrator does not "disappear" — it proxies messages between the user and the sub-agent, maintaining the single entry point principle. The Orchestrator can reclaim control at any time (timeout, budget exceeded, user request). The specific reclaim mechanism (state tracking in `MessageRouter`, cancellation protocol, message buffer handling on timeout) is deferred to Phase 2 design and documented in the Risks section.

#### Why not `autonomous` mode in MVP?

Autonomous agents (event-driven, background) are achievable via protocol composition: an extension implements both `AgentProvider` and `SchedulerProvider` (or `ServiceProvider`). The Loader already handles these protocols. No new kernel code is needed.

```python
class PriceMonitorAgent:
    """Implements AgentProvider + SchedulerProvider: runs on cron, uses LLM internally."""

    def get_schedule(self) -> str:
        return "0 */4 * * *"

    async def execute(self) -> dict[str, Any] | None:
        response = await self.invoke("Check prices and report anomalies")
        if response.status == "success" and response.content:
            return {"text": response.content}
        return None

    async def invoke(self, task: str, context: AgentInvocationContext | None = None) -> AgentResponse:
        result = await Runner.run(self._agent, task)
        return AgentResponse(status="success", content=result.final_output or "")

    def get_agent_descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            name="Price Monitor",
            description="Monitors prices on configured sources. Can be asked for current prices or run on schedule.",
            integration_mode="tool",
        )
```

This follows the existing architectural principle: capabilities are composed from protocols, not declared as types. Autonomous agents communicating via event subscriptions can potentially create unbounded event loops — this risk is mitigated by `SchedulerProvider` composition having inherent execution boundaries (cron tick interval), and is explicitly addressed with TTL/depth limits in Phase 3.

### 3. Declarative Agents: `entrypoint` Is Optional

Most agent-extensions follow the same pattern: load instructions, resolve tools from `uses_tools`, create an `Agent` instance, wrap `Runner.run()` in `invoke()`. Writing a `main.py` for this is pure boilerplate.

**The `entrypoint` field becomes optional for agent-extensions.** If a manifest has an `agent` section but no `entrypoint`, the Loader creates the agent automatically from manifest configuration using a built-in `DeclarativeAgentAdapter`. No Python code is needed.

#### Two loading paths

```
                  Has `agent` section in manifest?
                              │
                 ┌────────────┴────────────┐
                 │                          │
                NO                         YES
                 │                          │
                 ▼                          │
        Standard extension           Has `entrypoint`?
        (ToolProvider, etc.)                │
                               ┌────────────┴────────────┐
                               │                          │
                              YES                         NO
                               │                          │
                               ▼                          ▼
                    Programmatic Agent          Declarative Agent
                    (load class from            (Loader creates
                     main.py as before)          DeclarativeAgentAdapter)
```

#### `DeclarativeAgentAdapter` — built into the kernel

The kernel provides a small built-in class (~40 lines) that implements `AgentProvider` and the `Extension` lifecycle:

```python
class DeclarativeAgentAdapter:
    """AgentProvider created from manifest.yaml — no main.py needed."""

    async def initialize(self, context: ExtensionContext) -> None:
        instructions = self._resolve_instructions(self._manifest.agent.instructions)
        self._agent = Agent(
            name=self._manifest.name,
            instructions=instructions,
            model=self._manifest.agent.model,
            tools=context.resolved_tools,
        )

    async def invoke(self, task: str, context: AgentInvocationContext | None = None) -> AgentResponse:
        result = await Runner.run(
            self._agent, task,
            max_turns=self._manifest.agent.limits.max_turns,
        )
        return AgentResponse(
            status="success",
            content=result.final_output or "",
        )

    def get_agent_descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            name=self._manifest.name,
            description=self._manifest.description,
            integration_mode=self._manifest.agent.integration_mode,
        )
```

The adapter reads everything from the manifest: model, instructions (file path or inline), tools (resolved by Loader from `uses_tools`), limits. It handles Jinja2 template rendering for instructions. This is not a separate class hierarchy — it is a thin adapter that wires manifest config to the OpenAI Agents SDK.

#### When `main.py` is still needed

| Scenario | Why manifest is not enough |
|----------|---------------------------|
| Custom pre/post-processing around LLM calls | Logic before `invoke()` or after getting the result |
| Protocol composition (`AgentProvider` + `SchedulerProvider`) | `execute()` and `get_schedule()` require code |
| Custom tools defined inside the agent | Tools that don't come from another extension |
| Dynamic instructions (runtime data in prompts) | Instructions depend on state beyond Jinja2 variables |
| Event-driven behavior (subscribe to events) | Requires `initialize()` with `context.subscribe()` |
| Complex context assembly (RAG, memory queries) | Custom logic to build the `task` string |

For these cases, the developer provides `entrypoint: main:ClassName` as before, and the class implements `AgentProvider` directly. The manifest `agent` section still serves as configuration — the programmatic agent reads `context.config` and `context.resolved_tools` but adds its own logic on top.

#### Impact on Builder Agent

Declarative agents radically simplify the Builder Agent's job. To create a new agent, the Builder writes:
1. `manifest.yaml` with the `agent` section.
2. A prompt file (`.jinja2` or `.md`).

No `main.py`. No Python code generation. No syntax errors. This significantly reduces the failure rate of agent creation and lowers the bar to "write YAML + a prompt."

### 4. Manifest Extension: `agent` Section

Agent-extensions declare their AI configuration in an `agent` section of `manifest.yaml`.

#### Declarative agent example (no `entrypoint`)

```yaml
id: code_reviewer
name: Code Review Agent
version: "1.0.0"
description: >
  AI agent specialized in code review: analyzes diffs, finds bugs,
  suggests improvements, checks style compliance.

description: |
  Use this agent when the user asks to review code, analyze a diff,
  or check code quality. The agent reads files, analyzes code structure,
  and returns a detailed review with actionable suggestions.
  DO NOT use for writing new code — use the Builder Agent instead.

agent:
  integration_mode: tool
  model: gpt-4o-mini
  instructions: prompts/code_reviewer.jinja2

  parameters:
    temperature: 0.2
    max_output_tokens: 4000

  uses_tools:
    - kv
    - file_manager

  limits:
    max_turns: 10
    max_tokens_per_invocation: 20000

depends_on:
  - kv

enabled: true
```

Note: no `entrypoint` field. The Loader will create a `DeclarativeAgentAdapter` automatically.

#### Programmatic agent example (with `entrypoint`)

```yaml
id: builder_agent
name: Extension Builder Agent
version: "1.0.0"
description: >
  AI agent that creates new extensions for the application.
  Generates manifest.yaml and prompt files, then requests restart.
entrypoint: main:BuilderAgentExtension

description: |
  Use this agent when the user asks to create a new extension, plugin,
  tool, channel, or agent. The Builder generates code following the
  extension contract and requests a system restart to activate it.
  DO NOT use for modifying existing extensions.

agent:
  integration_mode: tool
  model: gpt-5.2-codex
  instructions: prompts/builder.jinja2
  uses_tools:
    - file_manager
  limits:
    max_turns: 20

depends_on: []

enabled: true
```

The `entrypoint` field signals the Loader to load a custom class. The `agent` section still provides configuration that the programmatic extension reads via `context.config` or `ExtensionContext`.

#### Manifest fields — `agent` section

| Field | Required | Description |
|-------|----------|-------------|
| `integration_mode` | Yes | `tool` or `handoff` — how Orchestrator interacts with this agent |
| `model` | Yes | Model identifier (e.g. `gpt-4o-mini`, `gpt-5.2`, `claude-sonnet-4-20250514`) |
| `instructions` | Yes (declarative) | System prompt: path to `.jinja2`/`.md` file, or inline text. For declarative agents this is the only source of behavior |
| `parameters` | No | Model parameters: `temperature`, `max_output_tokens`, `timeout_ms`, etc. |
| `uses_tools` | No | Allowlist of extension IDs whose tools this agent can access. Empty = no external tools |
| `limits` | No | Guardrails: `max_turns`, `max_tokens_per_invocation`, `time_budget_ms` |

The `agent` section is optional in `ExtensionManifest`. Its presence signals the Loader to treat this extension as an agent. Extensions without `agent` section work exactly as before — no breaking changes.

#### Instructions resolution

The `instructions` field supports three formats:

1. **File path** — `prompts/code_reviewer.jinja2` — loaded relative to extension dir, then project root. Jinja2 templates are rendered with manifest config as variables.
2. **Inline text** — multi-line YAML string directly in the manifest. For simple agents with short prompts.
3. **Empty/omitted** — agent runs with no system prompt (rare, but valid for extremely generic agents).

#### Tool isolation via `uses_tools`

A critical design constraint: **agent-extensions see only the tools explicitly listed in `uses_tools`**. The Loader resolves these IDs to actual `ToolProvider` instances and passes only their tools to the agent during initialization. The Orchestrator does not see tools that belong exclusively to sub-agents.

This prevents tool explosion on the Orchestrator and provides a security boundary: a code review agent cannot accidentally invoke `shell_exec` unless explicitly allowed.

### 5. Loader Changes

The Loader gains awareness of `AgentProvider` in `detect_and_wire_all()`:

```python
if isinstance(ext, AgentProvider):
    self._agent_providers[ext_id] = ext
```

For declarative agents (manifest has `agent` section, no `entrypoint`), the Loader creates a `DeclarativeAgentAdapter` instance during `load_all()` instead of importing a class from `main.py`:

```python
def _load_one(self, manifest: ExtensionManifest) -> Extension:
    if manifest.agent and not manifest.entrypoint:
        return DeclarativeAgentAdapter(manifest)
    # ... existing dynamic import logic ...
```

A new method `get_agent_tools()` wraps each `AgentProvider` (in `tool` mode) as a function tool for the Orchestrator:

```python
def get_agent_tools(self) -> list[Any]:
    """Wrap AgentProvider extensions as callable tools for the Orchestrator."""
    tools = []
    for ext_id, ext in self._agent_providers.items():
        descriptor = ext.get_agent_descriptor()
        if descriptor.integration_mode == "tool":
            tools.append(self._wrap_agent_as_tool(ext_id, ext, descriptor))
    return tools
```

The wrapper creates a `@function_tool` with the agent's `description` as the tool description and `invoke()` as the handler. The tool name follows the convention `agent_<ext_id>` to avoid collisions with regular tools. The Orchestrator sees it as just another tool — no special routing logic needed.

For `handoff` mode, the Loader registers the agent with the `MessageRouter` so the Orchestrator can delegate conversation turns.

### 6. Builder Agent Migration

`core/agents/builder.py` is removed from core and becomes a standard agent-extension at `sandbox/extensions/builder_agent/`.

The Builder Agent is a **programmatic agent** (requires `entrypoint`) because it needs custom tools (`file`, `apply_patch_tool`, `request_restart`) that are defined inside the extension, not imported from other extensions. It internally creates an `Agent` with these tools — exactly as it does now, but encapsulated inside the extension.

With declarative agents, the Builder's job becomes simpler: to create a new agent, it writes `manifest.yaml` + a prompt file. No `main.py` generation needed for most agents.

**Self-hosting test**: if the Builder Agent (as an extension) can create a new declarative agent-extension that the system loads and runs — the architecture is validated.

### 7. Runner Changes

`core/runner.py` changes minimally:

```python
agent = create_orchestrator_agent(
    extension_tools=loader.get_all_tools(),
    agent_tools=loader.get_agent_tools(),          # new
    capabilities_summary=loader.get_capabilities_summary(),
)
```

`create_orchestrator_agent` merges core tools, extension tools, and agent tools into one list. From the Orchestrator's perspective, agent-tools are just tools.

### 8. Tool Resolution for Agent-Extensions

During `initialize_all()`, the Loader resolves `uses_tools` from the manifest:

1. Read `manifest.agent.uses_tools` — list of extension IDs.
2. For each ID, find the loaded `ToolProvider` extension.
3. Call `get_tools()` on each, collect the results.
4. Pass the collected tools to the agent-extension via `ExtensionContext`.

The `ExtensionContext` gains a new field:

```python
class ExtensionContext:
    resolved_tools: list[Any]  # tools from uses_tools, resolved by Loader
```

Both declarative and programmatic agent-extensions use `context.resolved_tools`. The `DeclarativeAgentAdapter` passes them to the SDK `Agent` constructor. Programmatic agents can augment them with custom tools.

### 9. ExtensionManifest Changes

`ExtensionManifest` (Pydantic model) gains an optional `agent` section. The `entrypoint` field becomes optional:

```python
class AgentManifestConfig(BaseModel):
    integration_mode: Literal["tool", "handoff"] = "tool"
    model: str
    instructions: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    uses_tools: list[str] = Field(default_factory=list)
    limits: AgentLimits = Field(default_factory=AgentLimits)

class AgentLimits(BaseModel):
    max_turns: int = 10
    max_tokens_per_invocation: int = 50000
    time_budget_ms: int = 120000

class ExtensionManifest(BaseModel):
    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    entrypoint: str | None = None  # optional for declarative agents
    description: str = ""
    setup_instructions: str = ""
    depends_on: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    agent: AgentManifestConfig | None = None
```

Validation rules:
- Non-agent extensions still require `entrypoint` (they have custom code by definition).
- Agent-extensions without `entrypoint` must have `agent.model` and `agent.instructions` (the Loader needs these to create a `DeclarativeAgentAdapter`).
- Agent-extensions with `entrypoint` may omit `agent.instructions` (the code handles it).

### 10. Capabilities Summary

`get_capabilities_summary()` is updated to distinguish between tool-extensions and agent-extensions in the Orchestrator's system prompt:

```
Available tools:
- kv: Key-value storage for persistent data...
- web_search: Search the web for information...

Available agents:
- code_reviewer: Use this agent when the user asks to review code...
- builder_agent: Use this agent when the user asks to create a new extension...
```

This helps the Orchestrator make better routing decisions. The agent descriptions come from `description` in the manifest — these should be written for LLM consumption, not human documentation. Good descriptions include: when to use, when NOT to use, what the agent can and cannot do.

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                       SUPERVISOR                              │
│                  spawn · monitor · restart                     │
└──────────────────────────┬───────────────────────────────────┘
                           │ subprocess
┌──────────────────────────▼───────────────────────────────────┐
│                       NANO-KERNEL                             │
│                                                               │
│   Loader ──► Orchestrator Agent ──► MessageRouter            │
│     │              │                (existing, from ADR 002)  │
│     │    ┌─────────┼──────────┐                              │
│     │    │ tools   │ tools    │ agent-as-tool                │
│     │    ▼         ▼          ▼                              │
│     │  ┌───────┐ ┌───────┐ ┌───────────────┐                │
│     │  │  kv   │ │ shell │ │ code_reviewer │ Declarative    │
│     │  │(Tool) │ │(Tool) │ │  model: mini  │ AgentProvider  │
│     │  └───────┘ └───────┘ │  tools: [kv]  │ (no main.py)  │
│     │                      └───────────────┘                │
│     │                                                        │
│     │  ┌────────────────┐  ┌─────────────────┐              │
│     │  │  cli_channel   │  │ builder_agent   │ Programmatic │
│     │  │ (Channel)      │  │  model: codex   │ AgentProvider│
│     │  └────────────────┘  │  tools: [file]  │ (main.py)   │
│     │                      └─────────────────┘              │
│     │                                                        │
│     └─── DeclarativeAgentAdapter (built into kernel)        │
│          Creates AgentProvider from manifest when no         │
│          entrypoint is specified                             │
└──────────────────────────────────────────────────────────────┘
```

## Implementation Plan

### Phase 1: Foundation (MVP)

1. Add `AgentProvider` protocol, `AgentDescriptor`, `AgentResponse`, `AgentInvocationContext` to `contract.py`.
2. Add `AgentManifestConfig` and `AgentLimits` to `manifest.py`. Make `entrypoint` optional.
3. Implement `DeclarativeAgentAdapter` in kernel (~40 lines).
4. Update `Loader._load_one()` — two loading paths (declarative vs programmatic).
5. Update `Loader.detect_and_wire_all()` to detect `AgentProvider`.
6. Implement `Loader.get_agent_tools()` — wrap agents as function tools.
7. Add `resolved_tools` to `ExtensionContext`.
8. Update `runner.py` to pass agent tools to Orchestrator.
9. Update `get_capabilities_summary()` to separate tools and agents.
10. Migrate Builder Agent from `core/agents/builder.py` to `sandbox/extensions/builder_agent/`.
11. Remove `core/agents/builder.py` from core.

### Phase 2: Handoff Mode

1. Design handoff state machine: active agent tracking, message proxying, reclaim triggers.
2. Implement handoff protocol in `MessageRouter` — proxied conversation delegation.
3. Add budget enforcement: `max_turns` and token tracking per invocation via `AgentResponse` metrics.

### Phase 3: Guardrails & Observability

1. Agent event tracing: `agent_invoked`, `agent_result`, `agent_error` events via pub/sub, linked by `correlation_id` from `AgentInvocationContext`.
2. Context isolation enforcement: sub-agents receive only task-specific context, not full conversation history.
3. TTL/depth limits on event chains to prevent infinite loops in event-driven agents (protocol-composed autonomous agents).
4. Per-agent budget tracking: cumulative token/invocation counts based on `AgentResponse.tokens_used`.

## Consequences

### What changes

| Before | After |
|--------|-------|
| Builder Agent in `core/agents/` | Builder Agent is an extension in `sandbox/extensions/` |
| All tools registered on Orchestrator | Agent-extensions encapsulate their own tool subsets |
| One model for all agent work | Per-agent model configuration via manifest |
| No sub-agent capability | Extensions can be full AI agents with own instructions/tools |
| `entrypoint` required for all extensions | `entrypoint` optional for agent-extensions (declarative agents) |
| Creating an agent requires Python code | Declarative agents need only `manifest.yaml` + a prompt file |

### What stays the same

- Orchestrator remains the single brain in core — the only agent the kernel creates directly.
- Extension lifecycle (discover → load → initialize → wire → start) is unchanged.
- Existing protocols (`ToolProvider`, `ChannelProvider`, etc.) work exactly as before.
- Manifest-first approach: everything is declared in YAML.
- Protocol detection via `isinstance()` — no type field in manifest.
- Extensions still cannot import from `core/` — only through `ExtensionContext`.

### Trade-offs

- **Gained:** sub-agent specialization, tool isolation, model flexibility, kernel purity (Builder out of core), composable autonomy via protocol combination, declarative agent creation without code.
- **Deferred:** full autonomous mode as a dedicated protocol (achievable via `AgentProvider` + `SchedulerProvider`/`ServiceProvider` composition), memory scoping (requires memory system first), agent-to-agent direct communication.
- **Kernel size impact:** approximately +40 lines for `DeclarativeAgentAdapter`, +30 lines in Loader, +15 lines in manifest, +20 lines in contract. The nano-kernel stays nano.

### Known risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Handoff reclaim complexity** — how Orchestrator technically interrupts a sub-agent loop, where "active agent" state lives, what happens to buffered messages on timeout | Medium | Deferred to Phase 2 design. Phase 1 only implements `tool` mode, which has no reclaim problem. |
| **Event loop storms** — autonomous agents (via protocol composition) reacting to each other's events through pub/sub | Medium | `SchedulerProvider` composition has inherent cron-tick boundaries. Explicit TTL/depth limits added in Phase 3. |
| **Large agent responses** — sub-agent returns 50K+ tokens, overflowing Orchestrator's context | Low | `AgentResponse.content` can be truncated/summarized by the Orchestrator. `max_output_tokens` in manifest `parameters` limits LLM output at source. |
| **Tool name collisions** — agent-as-tool names clashing with regular tool names | Low | Convention: agent tools are named `agent_<ext_id>`. Loader validates uniqueness at wire time. |

## Alternatives Considered

**Dedicated `autonomous` protocol** — a third integration mode for event-driven agents. Rejected because protocol composition (`AgentProvider` + `SchedulerProvider`/`ServiceProvider`) achieves the same result without adding kernel complexity. This is the strongest architectural decision in this ADR: composition over type explosion.

**`invoke()` returning `str`** — simpler contract but loses error handling, metrics, and retry capability. Rejected after expert review: the cost of adding `AgentResponse` is a single dataclass, while the cost of retrofitting structured returns later is high (all callers must change).

**Mandatory `main.py` for all agents** — consistent with existing extensions but creates unnecessary boilerplate for simple agents. The 80/20 split (80% declarative, 20% programmatic) means most agents benefit from manifest-only creation.

## Acceptance Criteria

1. **Self-hosting test:** Builder Agent (as a programmatic extension) creates a new declarative agent-extension. The system restarts, loads the new agent, and the Orchestrator can invoke it successfully.
2. **Tool isolation:** a declarative agent with `uses_tools: [kv]` can access `kv` tools but cannot access `shell_tool` or any tool not in its allowlist.
3. **Orchestrator prompt reduction:** after migrating N tool-extensions into agent-extensions, the Orchestrator's system prompt shrinks by the token count of those tools' descriptions.
4. **AgentResponse contract:** Orchestrator receives structured `AgentResponse` with `status`, handles `error` and `refused` statuses without crashing.

## References

- [OpenAI Agents SDK — Agents](https://openai.github.io/openai-agents-python/agents/)
- [OpenAI Agents SDK — Handoffs](https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md)
- [Microsoft Multi-Agent Reference Architecture — Patterns](https://microsoft.github.io/multi-agent-reference-architecture/docs/reference-architecture/Patterns.html)
- [Azure Architecture Center — AI Agent Orchestration Patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
- [Anthropic — Building Effective Agents](https://www.anthropic.com/news/building-effective-agents)
- ADR 001: Supervisor and AI Agent as Separate Processes
- ADR 002: Nano-Kernel + Extensions
