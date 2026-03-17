# ADR 033: Dynamic Agent Tool Assignment v1 (Strict IDs)

## Status

Accepted. Implemented.

## Context

Dynamic agents are created through delegation `create_agent`. In practice, the Orchestrator sometimes called `create_agent` with `tools=null`, which could produce an agent without required tools (for example web tasks without `web_search`). This caused silent degradation: the agent existed but could not complete external-data tasks.

The system already has a strong source of truth for available tools (`Loader` + `ToolProvider` extensions), so the missing part was strict validation and explicit semantics in `create_agent`.

## Decision

### 1. Strict semantics for `create_agent.tools`

`create_agent.tools` behavior is explicit:

- `tools is null` -> invalid; return `success=false` with actionable error
- `tools == []` -> explicitly create an agent without tools
- `tools == [ids...]` -> strict validation against available extension IDs

Strict policy: any unknown/unavailable tool ID causes failure (`success=false`). Partial validity also fails.

### 2. IDs only in v1

`create_agent.tools` accepts extension IDs only. Function-name aliases are out of scope for v1 to avoid ambiguity and hidden mapping complexity.

### 3. Tool catalog and discovery

Loader exposes tool catalog metadata (`description`) for available tools.  
`list_available_tools` returns:

- `tool_ids`
- `tool_descriptions`

This gives the Orchestrator enough data to choose explicit tool IDs before `create_agent`.

### 4. Delegation observability

`CreateAgentResult` includes:

- `tools_requested`
- `tools_assigned`
- `warnings`

This makes validation behavior visible to the Orchestrator and logs.

## Consequences

### Positive

- Eliminates silent creation of unusable dynamic agents.
- Forces explicit tool assignment decisions by the Orchestrator.
- Keeps Task Engine unchanged: it resolves the same provider object from `AgentRegistry`.

### Trade-offs

- `tools=null` no longer has implicit fallback behavior.
- Prompt quality becomes important so the Orchestrator always passes explicit IDs.

### Non-goals in v1

- Manifest-based intent inference metadata
- Function-name alias support in `create_agent.tools`
- `task_engine.submit_task` API changes
