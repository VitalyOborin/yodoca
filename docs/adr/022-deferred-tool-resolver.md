# ADR 022: Deferred Tool Resolver for Orchestrator

## Status

Accepted. Implemented

## Context

Orchestrator currently receives all tools from all `ToolProvider` extensions (`loader.get_all_tools()`), plus delegation and channel tools. As the number of extensions grows, tool schemas inflate the system context and increase latency/cost.

Observed issues:

- Large toolset is injected into each orchestrator turn even when only 1-2 tools are relevant.
- Tool overload degrades tool selection quality (irrelevant tools compete in the same context window).
- Product focus requires quick wins without architectural migration.

## Research (analogous solutions)

We reviewed 6 production patterns and prioritized by RICE (relative):

| Solution | What it does | Pros | Cons | RICE (relative) |
|---|---|---|---|---|
| OpenAI Agents `toolChoice` and forced tool policy | Constrains/forces tool usage for current turn | Explicit control, low complexity | Needs pre-selected candidate tools | 8.5 |
| Semantic Kernel Function Choice Behavior | Auto/Required/None function advertisement policies | Clear runtime control over exposed functions | Requires robust pre-filter step | 8.3 |
| LlamaIndex Router Retriever | Routes query to top candidate engines/tools | Proven top-k routing pattern | Needs index/catalog quality | 8.1 |
| Haystack ConditionalRouter | Deterministic routing by conditions | Predictable, easy to test | Rules can be brittle for fuzzy requests | 7.6 |
| AutoGen SelectorGroupChat | Select best agent by task context | Good for specialization and delegation | Added orchestration overhead | 7.2 |
| LangChain multi-agent supervisor/tool routing | Hierarchical routing to specialists | Scales with many capabilities | Higher complexity than needed now | 6.9 |

Primary references:

- https://openai.github.io/openai-agents-js/guides/tools/
- https://learn.microsoft.com/en-us/semantic-kernel/concepts/ai-services/chat-completion/function-calling/function-choice-behaviors
- https://docs.llamaindex.ai/en/stable/examples/retrievers/router_retriever/
- https://docs.haystack.deepset.ai/docs/conditionalrouter
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/selector-group-chat.html
- https://langchain-ai.github.io/langgraph/concepts/multi_agent/

## Decision

Adopt a two-stage Deferred Tool Resolver:

1. **Keep orchestrator toolset minimal**: remove direct extension tools from orchestrator.
2. **Add a gateway tool** `run_with_resolved_tools(...)`:
   - Resolve top-k relevant tool IDs from tool catalog (manifest metadata + lexical matching).
   - Create short-lived dynamic agent with only selected tool IDs.
   - Delegate task execution to that agent and return structured result.
3. Add helper tool `resolve_tools_for_task(...)` for visibility/debugging.
4. Keep existing delegation tools (`list_agents`, `delegate_task`, `create_agent`) for explicit control.

This combines the best parts of reviewed approaches:

- SK/OpenAI style constrained tool exposure per turn.
- LlamaIndex-style top-k candidate routing.
- Deterministic and testable routing like Haystack conditional pipelines.

## Architecture

### New components

- `core/agents/deferred_tool_resolver.py`
  - `ToolCatalogEntry` metadata model
  - `DeferredToolResolver` ranking engine
  - `make_deferred_tool_tools(...)` gateway tool factory

### Updated components

- `core/extensions/loader.py`
  - New `get_tool_catalog()` to expose manifest-derived metadata for tool providers.
- `core/runner.py`
  - Orchestrator no longer receives `loader.get_all_tools()`.
  - Orchestrator receives deferred gateway tools.
- `prompts/orchestrator.jinja2`
  - Explicit instruction: use deferred resolver for tool-heavy tasks.

## Implementation details

- Ranking uses weighted lexical scoring across:
  - extension id/name (high weight)
  - description/setup/events/config keys (medium weight)
- Selection rules:
  - Return top-k matching tool IDs.
  - Keep `core_tools` as fallback when no strong match.
  - Stable deterministic ordering for repeatability.
- Gateway execution:
  - Build dynamic agent via `AgentFactory`.
  - Invoke via `AgentRegistry`.
  - Return structured `DeferredExecutionResult`.

## Definition of Done

- Orchestrator no longer loads all extension tools by default.
- Deferred resolver tools are available to orchestrator.
- Resolver returns deterministic top-k tool IDs for a task.
- Gateway executes task successfully with resolved tools.
- Prompt updated to enforce resolver-first behavior.
- New unit tests pass.

## Test plan

1. Resolver scoring and fallback:
   - picks memory tool for memory-like query
   - falls back to `core_tools` when no match
2. Gateway execution:
   - creates dynamic agent with resolved tool IDs
   - delegates task and returns structured success payload
3. Loader catalog:
   - includes only tool providers and `core_tools`

## Consequences

Positive:

- Significant orchestrator context reduction.
- Better precision in tool selection.
- Scales to many extensions without system-prompt explosion.

Trade-offs:

- Extra indirection step for tool execution.
- Initial lexical resolver can miss rare intent phrasing; mitigated via configurable `max_tools` and explicit `resolve_tools_for_task` inspection.
