# ADR 023: Semantic Agent Selection via Local Vector Retrieval

## Status

Accepted. Implemented

## Context

After ADR 022, orchestrator tool context was reduced via deferred tool execution. The next bottleneck is agent selection for delegation: as the number of static and dynamic agents grows, orchestrator still relies on broad discovery (`list_agents`) and manual selection, which increases prompt pressure and selection errors.

Constraints:

- Single-process architecture.
- Embedded storage only (SQLite in `sandbox/data`).
- No Redis, Azure Search, or external retrieval infrastructure.
- Core must remain extension-agnostic.

## Decision

Implement semantic agent selection in-core using existing stack:

1. Add `sample_queries` to agent manifest (`agent.sample_queries`).
2. Introduce `SemanticAgentSelector` in `core/agents`:
   - Builds local vector index in SQLite (`sandbox/data/agent_selector.db`).
   - Embeds agent profiles using existing `EmbeddingCapability` from `ModelRouter`.
   - Falls back to deterministic lexical ranking when embeddings are unavailable.
3. Extend delegation toolset with:
   - `select_agents_for_task(task, top_k)`
   - `delegate_task_auto(task, context, top_k)`
4. Keep existing `list_agents` and `delegate_task` unchanged for explicit control.

## Architecture

### Agent profile

For each agent, selector indexes:

- `id`, `name`, `description`
- `tools`
- `sample_queries` (5+ recommended)
- `integration_mode`

### Retrieval

- **Primary**: cosine similarity between query embedding and mean profile embedding.
- **Fallback**: weighted lexical overlap over name/description/tools/sample_queries.
- **Output**: top-k ranked agent IDs with scores and strategy used (`semantic` or `lexical`).

### Storage

SQLite table stores current profile vectors and metadata. Rebuilt lazily when profile signature changes.

## Implementation details

- Selector uses `asyncio.Lock` to avoid concurrent rebuild races.
- Rebuild trigger based on deterministic signature of agent catalog snapshot.
- `delegate_task_auto` selects best candidate and calls `AgentRegistry.invoke(...)`.
- If no candidate is found, returns structured error result.

## Definition of Done

- Manifest supports `agent.sample_queries`.
- Local semantic selector works without external services.
- Delegation tools expose semantic selection and auto delegation.
- Runner wires selector with embedding capability and local SQLite path.
- Prompt instructs orchestrator to use semantic selection first.
- Tests cover semantic path, lexical fallback, and auto-delegation behavior.

## Test plan

1. Selector semantic ranking picks correct agent for query.
2. Selector lexical fallback works when embedding capability is absent.
3. `make_delegation_tools(..., selector=...)` adds new tools.
4. `delegate_task_auto` routes to top selected agent and returns structured result.
5. Full test suite remains green.

## Consequences

Positive:

- Lower orchestration overhead with many agents.
- Better delegation precision for ambiguous user tasks.
- No new infrastructure dependency.

Trade-offs:

- Selection quality depends on `sample_queries` quality.
- Embedding availability may vary by provider configuration; lexical fallback mitigates this.
