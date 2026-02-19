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

    async def invoke(self, task: str, context: dict[str, Any] | None = None) -> str:
        """Execute a task and return the result. Used in 'tool' mode."""
```

`AgentDescriptor` is a simple dataclass:

```python
@dataclass(frozen=True)
class AgentDescriptor:
    name: str
    description: str
    integration_mode: Literal["tool", "handoff"]
```

The protocol is intentionally minimal — only `get_agent_descriptor()` and `invoke()`. This is the MVP contract. The agent extension internally creates its own `Agent` instance (from `agents` SDK or any other library), with its own model, instructions, and tools. The kernel never touches these internals.

#### Integration Modes

| Mode | Behavior | Use case |
|------|----------|----------|
| **tool** | Orchestrator calls `invoke(task, context)` and receives a result string. The sub-agent runs to completion and returns. Orchestrator stays in control. | Domain-specific subtasks: code review, email triage, data analysis |
| **handoff** | Orchestrator delegates the full conversation turn to the sub-agent. The sub-agent interacts with the user through the Orchestrator (proxied, not direct). Orchestrator monitors and can reclaim control. | Complex multi-turn workflows: tech support, travel booking, guided setup |

**`tool` mode is the default and primary mode.** It maps directly to the OpenAI Agent-as-Tool pattern: the Orchestrator wraps the sub-agent as a function tool and calls it like any other tool. The sub-agent receives only the task description and optional context — not the full conversation history.

**`handoff` mode** is reserved for scenarios where the sub-agent needs multi-turn interaction with the user. The Orchestrator does not "disappear" — it proxies messages between the user and the sub-agent, maintaining the single entry point principle. The Orchestrator can reclaim control at any time (timeout, budget exceeded, user request).

#### Why not `autonomous` mode in MVP?

Autonomous agents (event-driven, background) are achievable via protocol composition: an extension implements both `AgentProvider` and `SchedulerProvider` (or `ServiceProvider`). The Loader already handles these protocols. No new kernel code is needed.

```python
class PriceMonitorAgent:
    """Implements AgentProvider + SchedulerProvider: runs on cron, uses LLM internally."""

    def get_schedule(self) -> str:
        return "0 */4 * * *"  # every 4 hours

    async def execute(self) -> dict[str, Any] | None:
        result = await self._agent.run("Check prices and report anomalies")
        if result:
            return {"text": result}
        return None

    async def invoke(self, task: str, context: dict[str, Any] | None = None) -> str:
        return await self._agent.run(task)

    def get_agent_descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            name="Price Monitor",
            description="Monitors prices on configured sources. Can be asked for current prices or run on schedule.",
            integration_mode="tool",
        )
```

This follows the existing architectural principle: capabilities are composed from protocols, not declared as types.

### 2. Manifest Extension: `agent` Section

Agent-extensions declare their AI configuration in an `agent` section of `manifest.yaml`:

```yaml
id: code_reviewer
name: Code Review Agent
version: "1.0.0"
description: >
  AI agent specialized in code review: analyzes diffs, finds bugs,
  suggests improvements, checks style compliance.
entrypoint: main:CodeReviewAgent

natural_language_description: |
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

#### Manifest fields — `agent` section

| Field | Required | Description |
|-------|----------|-------------|
| `integration_mode` | Yes | `tool` or `handoff` — how Orchestrator interacts with this agent |
| `model` | Yes | Model identifier (e.g. `gpt-4o-mini`, `gpt-5.2`, `claude-sonnet-4-20250514`) |
| `instructions` | No | Path to system prompt template (Jinja2 supported). Relative to extension dir or project root |
| `parameters` | No | Model parameters: `temperature`, `max_output_tokens`, `timeout_ms`, etc. |
| `uses_tools` | No | Allowlist of extension IDs whose tools this agent can access. Empty = no external tools |
| `limits` | No | Guardrails: `max_turns`, `max_tokens_per_invocation`, `time_budget_ms` |

The `agent` section is optional. Its presence signals the Loader to check for `AgentProvider` protocol. Extensions without `agent` section work exactly as before — no breaking changes.

#### Tool isolation via `uses_tools`

A critical design constraint: **agent-extensions see only the tools explicitly listed in `uses_tools`**. The Loader resolves these IDs to actual `ToolProvider` instances and passes only their tools to the agent during initialization. The Orchestrator does not see tools that belong exclusively to sub-agents.

This prevents tool explosion on the Orchestrator and provides a security boundary: a code review agent cannot accidentally invoke `shell_exec` unless explicitly allowed.

### 3. Loader Changes

The Loader gains awareness of `AgentProvider` in `detect_and_wire_all()`:

```python
if isinstance(ext, AgentProvider):
    self._agent_providers[ext_id] = ext
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

The wrapper creates a `@function_tool` with the agent's `description` as the tool description and `invoke()` as the handler. The Orchestrator sees it as just another tool — no special routing logic needed.

For `handoff` mode, the Loader registers the agent with the `MessageRouter` so the Orchestrator can delegate conversation turns.

### 4. Builder Agent Migration

`core/agents/builder.py` is removed from core and becomes a standard agent-extension at `sandbox/extensions/builder_agent/`.

```yaml
# sandbox/extensions/builder_agent/manifest.yaml
id: builder_agent
name: Extension Builder Agent
version: "1.0.0"
description: >
  AI agent that creates new extensions for the application.
  Generates manifest.yaml and main.py, then requests restart.
entrypoint: main:BuilderAgentExtension

natural_language_description: |
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

The Builder Agent extension internally creates an `Agent` with `file`, `apply_patch_tool`, `shell_tool`, `request_restart`, and `WebSearchTool` — exactly as it does now, but encapsulated inside the extension.

**Self-hosting test**: if the Builder Agent (as an extension) can create a new agent-extension that the system loads and runs — the architecture is validated. This is the ultimate acceptance criterion.

### 5. Runner Changes

`core/runner.py` changes minimally:

```python
agent = create_orchestrator_agent(
    extension_tools=loader.get_all_tools(),
    agent_tools=loader.get_agent_tools(),          # new
    capabilities_summary=loader.get_capabilities_summary(),
)
```

`create_orchestrator_agent` merges core tools, extension tools, and agent tools into one list. From the Orchestrator's perspective, agent-tools are just tools.

### 6. Tool Resolution for Agent-Extensions

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

The agent-extension uses `context.resolved_tools` when creating its internal `Agent` instance.

### 7. ExtensionManifest Changes

`ExtensionManifest` (Pydantic model) gains an optional `agent` section:

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
    # ... existing fields ...
    agent: AgentManifestConfig | None = None
```

### 8. Capabilities Summary

`get_capabilities_summary()` is updated to distinguish between tool-extensions and agent-extensions in the Orchestrator's system prompt:

```
Available tools:
- kv: Key-value storage for persistent data...
- web_search: Search the web for information...

Available agents:
- code_reviewer: Use this agent when the user asks to review code...
- builder_agent: Use this agent when the user asks to create a new extension...
```

This helps the Orchestrator make better routing decisions.

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
│                    │                                          │
│         ┌──────────┼──────────┐                              │
│         │ tools    │ tools    │ agent-as-tool                │
│         ▼          ▼          ▼                              │
│   ┌─────────┐ ┌────────┐ ┌──────────────┐                   │
│   │ kv tool │ │ shell  │ │ code_reviewer│ (AgentProvider)    │
│   └─────────┘ └────────┘ │  model: mini │                   │
│                           │  tools: [kv] │                   │
│                           └──────────────┘                   │
│                                                               │
│   ┌────────────────┐  ┌─────────────────┐                    │
│   │  cli_channel   │  │ builder_agent   │ (AgentProvider)    │
│   │ (Channel)      │  │  model: codex   │                    │
│   └────────────────┘  │  tools: [file]  │                    │
│                        └─────────────────┘                    │
└──────────────────────────────────────────────────────────────┘
```

## Implementation Plan

### Phase 1: Foundation (MVP)

1. Add `AgentProvider` protocol and `AgentDescriptor` to `contract.py`.
2. Add `AgentManifestConfig` and `AgentLimits` to `manifest.py`.
3. Update `Loader.detect_and_wire_all()` to detect `AgentProvider`.
4. Implement `Loader.get_agent_tools()` — wrap agents as function tools.
5. Add `resolved_tools` to `ExtensionContext`.
6. Update `runner.py` to pass agent tools to Orchestrator.
7. Update `get_capabilities_summary()` to separate tools and agents.
8. Migrate Builder Agent from `core/agents/builder.py` to `sandbox/extensions/builder_agent/`.
9. Remove `core/agents/builder.py` from core.

### Phase 2: Handoff Mode

1. Implement handoff protocol in `MessageRouter` — proxied conversation delegation.
2. Add handoff lifecycle: Orchestrator delegates, sub-agent handles turns, Orchestrator reclaims.
3. Add budget enforcement: max_turns and token tracking per invocation.

### Phase 3: Guardrails & Observability

1. Agent event tracing: `agent_invoked`, `agent_result`, `agent_error` events via pub/sub.
2. Context isolation: sub-agents receive only task-specific context, not full conversation history.
3. Correlation IDs for tracing agent chains.
4. TTL/depth limits on event chains to prevent infinite loops in event-driven agents.

## Consequences

### What changes

| Before | After |
|--------|-------|
| Builder Agent in `core/agents/` | Builder Agent is an extension in `sandbox/extensions/` |
| All tools registered on Orchestrator | Agent-extensions encapsulate their own tool subsets |
| One model for all agent work | Per-agent model configuration via manifest |
| No sub-agent capability | Extensions can be full AI agents with own instructions/tools |

### What stays the same

- Orchestrator remains the single brain in core — the only agent the kernel creates directly.
- Extension lifecycle (discover → load → initialize → wire → start) is unchanged.
- Existing protocols (`ToolProvider`, `ChannelProvider`, etc.) work exactly as before.
- Manifest-first approach: everything is declared in YAML.
- Protocol detection via `isinstance()` — no type field in manifest.
- Extensions still cannot import from `core/` — only through `ExtensionContext`.

### Trade-offs

- **Gained:** sub-agent specialization, tool isolation, model flexibility, kernel purity (Builder out of core), composable autonomy via protocol combination.
- **Deferred:** full autonomous mode as a dedicated protocol (achievable via `AgentProvider` + `SchedulerProvider`/`ServiceProvider` composition), memory scoping (requires memory system first), agent-to-agent direct communication.
- **Risk:** added complexity in Loader for tool resolution. Mitigated by keeping the protocol minimal (2 methods) and deferring advanced features to later phases.
- **Kernel size impact:** minimal — approximately +30 lines in Loader, +15 lines in manifest, +10 lines in contract. The nano-kernel stays nano.

## References

- [OpenAI Agents SDK — Handoffs](https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md)
- [Microsoft Multi-Agent Reference Architecture — Patterns](https://microsoft.github.io/multi-agent-reference-architecture/docs/reference-architecture/Patterns.html)
- [Azure Architecture Center — AI Agent Orchestration Patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
- [Anthropic — Building Effective Agents](https://www.hkdca.com/wp-content/uploads/2025/05/building-effective-agents-anthropic.pdf)
- ADR 001: Supervisor and AI Agent as Separate Processes
- ADR 002: Nano-Kernel + Extensions
