# ADR 017: Agent Registry and Dynamic Delegation

## Status

Proposed

## Context

ADR 003 introduced `AgentProvider` and `DeclarativeAgentAdapter`, allowing extensions to provide specialized AI agents. This was a significant step — agents became extensions with their own tools, models, and instructions. However, the current wiring model has limitations that block the system's evolution toward SOTA multi-agent orchestration.

### Current wiring model

The Loader collects all `AgentProvider` extensions into `_agent_providers` and wraps each one as an individual tool for the Orchestrator via `get_agent_tools()`:

```
Loader._agent_providers → get_agent_tools() → [builder_agent_tool, simple_agent_tool, ...]
                                                         ↓
                                          Orchestrator(tools=[...all agent tools...])
```

Every AgentProvider with `integration_mode: "tool"` becomes a permanent tool in the Orchestrator's context. The Orchestrator sees agents as opaque tools with fixed names (`builder_agent`, `simple_agent`).

### Problems

| Problem | Description | Impact |
|---------|-------------|--------|
| **Hardcoded agent tools** | Each agent is a separate tool in the Orchestrator's tool list, always present regardless of need | Context pollution; every tool call pays for unused agent descriptions in the prompt |
| **No agent discovery** | The Orchestrator has no way to query what agents are available, what they can do, or whether they are busy | Cannot make informed delegation decisions |
| **No delegation semantics** | Invoking an agent is syntactically identical to calling any tool; the Orchestrator lacks a conceptual model of "delegate a task to a specialist" | No tracking of who does what, no status awareness |
| **Static agent pool** | Agents exist only if defined as extensions at startup; no runtime creation | Cannot adapt to novel tasks by spawning specialized agents |
| **No busy/available tracking** | The system has no concept of agent state (working, idle, capacity) | Cannot make capacity-aware routing decisions |
| **Disconnected from task_engine** | task_engine already has `agent_id` on tasks but gets agent references via a separate hardcoded config (`agent_extensions`), not from a unified registry | Two sources of truth for "which agents exist" |

### Research context

The [Yodoca Architecture Research](../discovery/Yodoca%20Architecture%20Research.md) analyzed SOTA approaches (2025–2026) and identified key patterns:

- **AOrchestra (ICLR 2025)**: Models agents as a 4-tuple `⟨Instruction, Context, Tools, Model⟩`. The orchestrator dynamically creates specialized sub-agents per subtask. 16.28% improvement over strongest baselines.
- **Anthropic Orchestrator-Worker**: Lead agent (Opus) delegates to specialized workers (Sonnet). Each worker receives: objective, output format, tool guidance, task boundaries. Multi-agent outperforms single-agent by 90.2%.
- **Microsoft Multi-Agent Reference Architecture**: Defines Agent Registry with Register, Repository, View, and Monitor components. The registry enables orchestrators to discover and select agents by capabilities.
- **Symphony**: Hierarchical sub-agent creation with autonomous system prompt generation per subtask.

The common pattern across all SOTA systems: **a central registry of agent capabilities** + **explicit delegation semantics** + **dynamic agent selection by the orchestrator**.

### What we preserve

- **Protocol-based contracts** — `AgentProvider`, `AgentDescriptor`, `AgentResponse`, `AgentInvocationContext` from ADR 003 remain the foundation.
- **Declarative agents** — `DeclarativeAgentAdapter` continues to work; manifests are unchanged.
- **Extension isolation** — core does not depend on extensions; the registry is populated by Loader at startup.
- **task_engine** — existing task queue, statuses, subtasks, human-in-the-loop remain intact and become a natural backend for async delegation.

## Decision

### 1. Agent Registry — core component

A new `AgentRegistry` class is added to core as an infrastructure component, analogous to `ModelRouter` or `EventBus`. It holds metadata about available agents and provides a query API.

**Location:** `core/agents/registry.py`

```python
@dataclass(frozen=True)
class AgentRecord:
    """Agent metadata in the registry. Discovery-oriented, not execution-oriented."""
    id: str
    name: str
    description: str
    model: str | None = None
    integration_mode: Literal["tool", "handoff"] = "tool"
    tools: list[str] = field(default_factory=list)
    limits: AgentLimits | None = None
    source: Literal["static", "dynamic"] = "static"


class AgentRegistry:
    """Central registry of available agents. Populated by Loader, queried by Orchestrator tools."""

    def register(self, record: AgentRecord, provider: AgentProvider) -> None:
        """Register an agent. Called by Loader for each AgentProvider extension."""

    def unregister(self, agent_id: str) -> None:
        """Remove an agent from the registry."""

    def get(self, agent_id: str) -> tuple[AgentRecord, AgentProvider] | None:
        """Get agent record and provider by id."""

    def list_agents(self, available_only: bool = False) -> list[AgentRecord]:
        """List all registered agents, optionally filtering by availability."""

    async def invoke(
        self,
        agent_id: str,
        task: str,
        context: AgentInvocationContext | None = None,
    ) -> AgentResponse:
        """Resolve agent by id and invoke. Tracks active invocations."""
```

**Why core, not extension:**

- The Loader already holds `_agent_providers` — the registry is a formalization of this existing dict with richer metadata and a proper API.
- `ModelRouter` and `EventBus` are core components for the same reason: they are infrastructure that multiple parts of the system depend on (Loader, Orchestrator, task_engine).
- An extension-based registry would create circular dependency issues (registry depends on agents, agents are extensions, tools for Orchestrator come from extensions).

**Minimal footprint:** The registry is a single class with in-memory storage (dict). No database, no persistence for MVP. This keeps the nano-kernel nano.

### 2. AgentRecord — agent metadata

`AgentRecord` holds discovery-oriented metadata. It is intentionally separate from `AgentDescriptor` (which is the extension's self-description) and from `AgentSpec` (the 4-tuple for dynamic creation, introduced in Phase 2).

| Field | Source | Purpose |
|-------|--------|---------|
| `id` | manifest `id` | Unique identifier for delegation |
| `name` | manifest `name` | Human-readable name |
| `description` | manifest `description` | LLM-readable: when to use this agent |
| `model` | manifest `agent.model` | Informs cost/capability reasoning |
| `integration_mode` | manifest `agent.integration_mode` | "tool" (synchronous) or "handoff" (conversation transfer) |
| `tools` | manifest `agent.uses_tools` | What tools the agent has access to |
| `limits` | manifest `agent.limits` | max_turns, max_tokens, time_budget |
| `source` | "static" or "dynamic" | How the agent was created |

### 3. Orchestrator tools — replacing agent-as-tool with delegation

The current pattern (one tool per agent) is replaced with two generic tools that interact with the registry.

**Remove:** `loader.get_agent_tools()` is no longer called in `runner.py`. Individual agent tools (`builder_agent`, `simple_agent`) are no longer injected into the Orchestrator.

**Add:** Two new tools provided by a factory function `make_delegation_tools(registry)`:

#### `list_agents` tool

```python
@function_tool
async def list_agents() -> ListAgentsResult:
    """List available agents with their capabilities.
    Use to discover which agent is best suited for a task before delegating."""
```

Returns structured data: `[{id, name, description, model, tools, limits}]`. The Orchestrator reads this to decide whether to delegate and to whom. This is **on-demand context** — the agent descriptions are loaded into context only when the Orchestrator actually considers delegation, not on every turn.

#### `delegate_task` tool

```python
@function_tool
async def delegate_task(
    agent_id: str,
    task: str,
    context: str = "",
) -> DelegateTaskResult:
    """Delegate a task to a specialized agent by id.
    Use after list_agents to pick the right agent for the job.
    The agent executes the task and returns the result."""
```

Synchronous delegation: resolves `agent_id` via registry → invokes `AgentProvider.invoke(task, invocation_context)` → returns `AgentResponse` wrapped in a structured result.

**Why two tools instead of one:** Separating discovery (`list_agents`) from execution (`delegate_task`) matches how humans work — first assess who can do the job, then assign it. It also means the Orchestrator can list agents without committing to delegation (e.g., to answer "what agents do I have?").

### 4. Capabilities summary — registry-aware

`loader.get_capabilities_summary()` is updated. Agent descriptions are no longer listed in the summary as individual tools. Instead, the summary includes a brief note that agents are available via `list_agents`:

```
Available tools:
- kv: Key-value storage for persistent data...
- web_search: Search the web for information...

Agent delegation:
Use list_agents to discover available specialized agents.
Use delegate_task to assign work to an agent.
```

This replaces the current "Available agents:" section that listed each agent's full description. The Orchestrator loads agent details on demand via `list_agents`, keeping the base system prompt compact.

### 5. Integration with task_engine — async delegation

For simple, fast tasks: `delegate_task` is synchronous (invoke agent, wait for result, return).

For long-running tasks: the Orchestrator continues to use `submit_task(goal, agent_id)` from task_engine. The key change is that task_engine resolves `agent_id` through the shared `AgentRegistry` instead of its own `agent_extensions` config.

```
Sync delegation:     Orchestrator → delegate_task(agent_id, task) → registry.invoke() → AgentResponse
Async delegation:    Orchestrator → submit_task(goal, agent_id)   → task_engine worker → registry.invoke() → task.completed
```

task_engine gains access to the registry via ExtensionContext (or direct injection). Its `worker.py` resolves agents from the registry instead of from `agent_extensions` + `ctx.get_extension()`:

```python
# Before (worker.py):
agent_ext = self._agent_registry.get(task.agent_id)  # local dict from config

# After:
record = self._registry.get(task.agent_id)  # AgentRegistry lookup
if record:
    response = await self._registry.invoke(task.agent_id, prompt, invocation_context)
```

The `agent_extensions` config field in task_engine manifest becomes unnecessary — the registry is the single source of truth for available agents.

### 6. Active invocation tracking

The registry tracks active synchronous invocations with a simple counter per agent:

```python
class AgentRegistry:
    _active: dict[str, int]  # agent_id → number of active invocations

    async def invoke(self, agent_id, task, context):
        self._active[agent_id] = self._active.get(agent_id, 0) + 1
        try:
            response = await provider.invoke(task, context)
            return response
        finally:
            self._active[agent_id] -= 1

    def is_busy(self, agent_id: str) -> bool:
        return self._active.get(agent_id, 0) > 0
```

For async tasks running in task_engine, "busy" status can be derived by querying task_engine for active tasks with a given agent_id. The registry does not duplicate this — it only tracks its own synchronous invocations.

`list_agents(available_only=True)` filters out agents that are currently busy (active invocations > 0).

### 7. Loader changes

The Loader populates the registry during `detect_and_wire_all()`:

```python
def detect_and_wire_all(self, router: MessageRouter, registry: AgentRegistry) -> None:
    for ext_id, ext in self._extensions.items():
        # ... existing protocol detection ...
        if isinstance(ext, AgentProvider):
            descriptor = ext.get_agent_descriptor()
            manifest = self._get_manifest(ext_id)
            record = AgentRecord(
                id=ext_id,
                name=descriptor.name,
                description=descriptor.description,
                model=manifest.agent.model if manifest.agent else None,
                integration_mode=descriptor.integration_mode,
                tools=manifest.agent.uses_tools if manifest.agent else [],
                limits=manifest.agent.limits if manifest.agent else None,
                source="static",
            )
            registry.register(record, ext)
```

**Removed:** `self._agent_providers` dict in Loader is replaced by the registry. `get_agent_tools()` and `_wrap_agent_as_tool()` methods are removed.

**Retained:** `_collect_tool_agent_parts()` for capabilities summary is updated to exclude agents (they are now in the registry, not in the summary as individual items).

### 8. Runner changes

```python
# core/runner.py — updated bootstrap

registry = AgentRegistry()
# ... Loader setup ...
loader.detect_and_wire_all(router, registry)

delegation_tools = make_delegation_tools(registry)
agent = create_orchestrator_agent(
    model_router=model_router,
    settings=settings,
    extension_tools=loader.get_all_tools(),
    # agent_tools=loader.get_agent_tools(),  — REMOVED
    delegation_tools=delegation_tools,       # NEW
    capabilities_summary=loader.get_capabilities_summary(),
    channel_tools=channel_tools,
)
```

### 9. Orchestrator prompt update

The Orchestrator prompt (`sandbox/prompts/orchestrator.jinja2`) gets a brief section on delegation:

```
## Delegation
When a task requires specialized knowledge, a different model, or would benefit from focused execution:
1. Call list_agents to see available agents
2. Choose the best-fit agent based on description and capabilities
3. Call delegate_task with a clear task description

Handle simple requests yourself. Delegate when:
- The task matches an agent's specialization (e.g. code generation → builder)
- A different model would produce better results
- The task is complex enough to benefit from a dedicated agent with focused tools
```

### 10. Foundation for dynamic agent creation (Phase 2)

The registry is designed to accept both static agents (from manifests) and dynamic agents (created at runtime). Phase 2 introduces `AgentSpec` — the AOrchestra-inspired 4-tuple:

```python
@dataclass(frozen=True)
class AgentSpec:
    """Four-tuple agent specification for dynamic creation (AOrchestra-inspired)."""
    instruction: str         # I: task objective + system prompt
    context: str | None      # C: curated working context
    tool_ids: list[str]      # T: extension IDs from ToolProvider registry
    model: str | None        # M: model identifier for ModelRouter
    limits: AgentLimits | None = None
```

A `create_agent(spec: AgentSpec) → AgentRecord` tool would:
1. Build an Agent from the spec (like DeclarativeAgentAdapter but ephemeral)
2. Register it in the registry with `source="dynamic"`
3. Return the agent_id for subsequent `delegate_task` calls
4. Auto-cleanup after task completion or TTL expiry

This is explicitly **out of scope for Phase 1** but the registry's `register/unregister` API and the `source` field on `AgentRecord` are designed to support it without breaking changes.

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                       NANO-KERNEL                            │
│                                                              │
│   Loader ──► Agent Registry ◄── task_engine (resolve agents) │
│     │              │                                         │
│     │    ┌─────────┴──────────┐                              │
│     │    │  register()        │                              │
│     │    │  list_agents()     │                              │
│     │    │  invoke()          │                              │
│     │    └─────────┬──────────┘                              │
│     │              │                                         │
│     │    ┌─────────┴──────────┐                              │
│     │    │   Orchestrator     │                              │
│     │    │   tools:           │                              │
│     │    │   - list_agents    │ ← delegation tools           │
│     │    │   - delegate_task  │                              │
│     │    │   - ext tools      │ ← ToolProvider tools         │
│     │    │   - core tools     │ ← file, restart, channels    │
│     │    │   - task tools     │ ← submit_task, etc.          │
│     │    └────────────────────┘                              │
│     │                                                        │
│     │          Agent Registry contents:                      │
│     │    ┌───────────────────────────────────────────┐       │
│     │    │ builder_agent  │ static │ codex    │ avail│       │
│     │    │ simple_agent   │ static │ gpt-mini │ avail│       │
│     │    │ (dynamic...)   │ dynamic│ sonnet   │ busy │ ← Ph2 │
│     │    └───────────────────────────────────────────┘       │
│     │                                                        │
│     └─── Populates registry during detect_and_wire_all()     │
└──────────────────────────────────────────────────────────────┘

Data flow — synchronous delegation:

  User: "Create a Slack extension"
    │
    ▼
  Orchestrator
    ├─ list_agents() → [{id: "builder_agent", desc: "creates extensions...", model: "codex"}]
    ├─ delegate_task(agent_id="builder_agent", task="Create Slack channel extension...")
    │     └─ registry.invoke("builder_agent", task, context)
    │           └─ AgentProvider.invoke(task) → AgentResponse(status="success", content="...")
    └─ Returns result to user

Data flow — async delegation via task_engine:

  User: "Research and build a Slack extension"
    │
    ▼
  Orchestrator
    ├─ list_agents() → [...]
    ├─ submit_task(goal="Research Slack API...", agent_id="builder_agent")
    │     └─ task_engine creates task (status=pending, agent_id="builder_agent")
    │           └─ worker claims task → registry.invoke("builder_agent", step_prompt)
    └─ "Task submitted, I'll notify you when it's done."
```

## Implementation Plan

### Phase 1: Agent Registry and Delegation (MVP)

1. **AgentRecord** — add frozen dataclass to `core/agents/registry.py`.
2. **AgentRegistry** — implement class with `register`, `unregister`, `get`, `list_agents`, `invoke`, `is_busy`. In-memory storage.
3. **Delegation tools** — `make_delegation_tools(registry)` factory in `core/agents/delegation_tools.py`. Two tools: `list_agents`, `delegate_task`. Pydantic result models (`ListAgentsResult`, `DelegateTaskResult`).
4. **Loader update** — `detect_and_wire_all` receives `AgentRegistry`, populates it from AgentProvider extensions. Remove `_agent_providers` dict, `get_agent_tools()`, `_wrap_agent_as_tool()`.
5. **Runner update** — instantiate `AgentRegistry`, pass to Loader and to `make_delegation_tools`. Replace `agent_tools` with `delegation_tools` in `create_orchestrator_agent`.
6. **Orchestrator factory update** — `create_orchestrator_agent` accepts `delegation_tools` instead of `agent_tools`.
7. **Capabilities summary update** — remove individual agent listings; add delegation note.
8. **Orchestrator prompt update** — add delegation guidance section.
9. **task_engine integration** — expose registry to task_engine via ExtensionContext or direct injection; worker resolves agents from registry. Remove `agent_extensions` config field.

### Phase 2: Dynamic Agent Creation (implemented)

1. **AgentSpec** — frozen dataclass in `core/agents/factory.py`: `name`, `instruction`, `tools` (list of extension IDs), `model` (optional), `max_turns`, `ttl_seconds`. Context field deferred (curated context in Phase 2.1).
2. **AgentFactory** — creates ephemeral `Agent` via OpenAI SDK, wraps in `DynamicAgentProvider`, registers in registry with `source="dynamic"`. Uses `ToolResolver` protocol (Loader's `resolve_tools`) and `ModelRouter.register_agent_config` + `get_model` for model resolution.
3. **`create_agent` tool** — Orchestrator tool in `make_delegation_tools`; returns `CreateAgentResult` with `agent_id`. Orchestrator can then `delegate_task` to it.
4. **`list_available_tools` tool** — returns tool IDs from Loader's `get_available_tool_ids()` (core_tools + ToolProvider extension IDs).
5. **Agent lifecycle** — `AgentRecord.expires_at`; `AgentRegistry.cleanup_expired()`; background `start_lifecycle_loop` (60s interval) in runner. Dynamic agents auto-removed after TTL (default 30 min).
6. **Context curation** — DEFERRED to Phase 2.1 (requires ContextCurator protocol and AOrchestra-style curated vs full context).

### Phase 3: Advanced Orchestration

1. **Parallel delegation** — IMPLEMENTED (Phase 3.1). Infrastructure already supported it (`parallel_tool_calls=True`, async `delegate_task`). Orchestrator prompt updated with guidance: issue multiple `delegate_task` calls in one turn for independent subtasks; use `submit_task` with shared `parent_task_id` for long-running parallel work.
2. **Task chains** — linked tasks where completion of one unblocks the next (leverages existing task_engine subtasks).
3. **Cost/capability routing** — Orchestrator uses model metadata from registry to optimize cost vs quality.
4. **Agent self-improvement** — agents refine their own tool descriptions (per Anthropic findings).

## Consequences

### What changes

| Before | After |
|--------|-------|
| Each AgentProvider is a separate tool in the Orchestrator | Two generic tools: `list_agents`, `delegate_task` |
| Agent descriptions always in Orchestrator context | Agent details loaded on-demand via `list_agents` |
| `Loader._agent_providers` dict + `get_agent_tools()` | `AgentRegistry` component in core |
| task_engine resolves agents from local `agent_extensions` config | task_engine resolves agents from shared `AgentRegistry` |
| No agent status tracking | Registry tracks active invocations per agent |
| No foundation for dynamic agents | Registry supports `source="dynamic"` records (Phase 2) |

### What stays the same

- `AgentProvider` protocol, `AgentDescriptor`, `AgentResponse`, `AgentInvocationContext` — unchanged.
- `DeclarativeAgentAdapter` — continues to work; manifests are unchanged.
- Extension lifecycle (discover → load → initialize → wire → start) — unchanged.
- Agent manifests (`builder_agent`, `simple_agent`) — no changes needed.
- task_engine tools (`submit_task`, `get_task_status`, etc.) — interface unchanged; internal agent resolution updated.
- Orchestrator remains the single brain; agents are invoked, not autonomous.

### Trade-offs

- **Gained:** cleaner Orchestrator context (no hardcoded agent tools), explicit delegation semantics, agent discovery, status tracking, single source of truth for agents, foundation for dynamic creation.
- **Cost:** one additional core component (~100 lines for registry + ~80 lines for delegation tools). Two-step delegation (list → delegate) adds one extra tool call compared to direct agent invocation.
- **Deferred:** context curation (Phase 2.1), task chains (Phase 3), cost/capability routing (Phase 3), agent self-improvement (Phase 3).

### Migration path

The change is backward-compatible at the manifest level — existing agent extensions (`builder_agent`, `simple_agent`) require no changes. The Orchestrator prompt is updated to use delegation tools instead of direct agent invocation. The task_engine's `agent_extensions` config becomes deprecated (agents are resolved from the shared registry).

### Known risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Two-step overhead** — `list_agents` + `delegate_task` is slower than direct tool call | Low | The Orchestrator can cache agent list across turns; for known agents it can call `delegate_task` directly without listing first. Prompt guidance helps. |
| **Registry as single point of failure** — if registry loses state, delegation breaks | Low | In-memory registry is populated at startup from manifests (stateless). Dynamic agents (Phase 2) will need persistence; address then. |
| **Orchestrator ignores delegation** — LLM may try to solve everything itself instead of delegating | Medium | Prompt engineering: clear delegation guidance with examples of when to delegate. Evaluate and iterate on prompt. |
| **task_engine coupling** — registry becomes a dependency for task_engine agent resolution | Low | task_engine already depends on core contracts. Registry access via ExtensionContext follows existing patterns (like `model_router`). |

## Alternatives Considered

### A. Agent Registry as an extension

Registry as a `ToolProvider` extension instead of a core component. Rejected because:
- Creates circular dependency: registry needs agent references from Loader, agents are extensions loaded by Loader.
- task_engine and other extensions need the registry — requires complex `depends_on` chains.
- The registry is infrastructure (like ModelRouter), not user-facing functionality.

### B. Keep agent-as-tool but add a meta-tool

Add `list_agents` tool while keeping individual agent tools. Rejected because:
- Does not solve context pollution — all agent descriptions are still in the prompt.
- Creates confusion: two ways to invoke an agent (direct tool call vs `delegate_task`).
- No foundation for dynamic agents.

### C. Full AOrchestra 4-tuple from day one

Implement dynamic agent creation with `AgentSpec` in MVP. Rejected because:
- Over-engineering for the first iteration; the static agent pool works for current use cases.
- Dynamic creation requires context curation, tool registry, agent lifecycle management — each is a significant feature.
- The registry design supports dynamic agents in Phase 2 without breaking changes; nothing is lost by deferring.

### D. Pure task_engine-based delegation (no sync path)

All delegation goes through task_engine (async). Rejected because:
- Simple tasks (e.g., "write a haiku") don't need background task overhead.
- The user expects an immediate response for quick delegations.
- Sync delegation via `delegate_task` + async via `submit_task` gives the Orchestrator flexibility.

## Acceptance Criteria

1. **Registry populated:** After startup, `registry.list_agents()` returns all AgentProvider extensions (builder_agent, simple_agent).
2. **Delegation works:** Orchestrator calls `delegate_task("builder_agent", "Create a hello-world extension")` and receives `AgentResponse` with the result.
3. **No agent tools in Orchestrator:** The Orchestrator's tool list contains `list_agents` and `delegate_task` but not `builder_agent` or `simple_agent` as individual tools.
4. **Context reduction:** The Orchestrator's system prompt no longer includes per-agent descriptions; they are available on-demand via `list_agents`.
5. **task_engine integration:** `submit_task(goal, agent_id="builder_agent")` resolves the agent through the registry, not through `agent_extensions` config.
6. **Status tracking:** `list_agents` shows an agent as busy while it is executing a delegated task.

## References

- ADR 003: Agent-as-Extension — foundation for AgentProvider, DeclarativeAgentAdapter
- [AOrchestra: Automating Sub-Agent Creation for Agentic Orchestration (ICLR 2025)](https://arxiv.org/html/2602.03786v2) — 4-tuple agent abstraction, dynamic sub-agent creation
- [Microsoft Multi-Agent Reference Architecture — Agent Registry](https://microsoft.github.io/multi-agent-reference-architecture/docs/agent-registry/Agent-Registry.html) — Register, Repository, View, Monitor components
- [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) — orchestrator-worker delegation patterns
- [Yodoca Architecture Research](../discovery/Yodoca%20Architecture%20Research.md) — SOTA analysis and transformation roadmap
