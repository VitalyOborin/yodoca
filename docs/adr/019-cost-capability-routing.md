# ADR 019: Cost/Capability Routing

## Status

Accepted. Implemented

## Context

ADR 017 introduced the Agent Registry and delegation tools (`list_agents`, `delegate_task`, `create_agent`). The Orchestrator receives agent metadata including a `model` field (e.g. `gpt-5-mini`, `gpt-5.2-codex`), but this is an opaque string. The Orchestrator has no structured data about:

- Relative cost of each model (cheap vs expensive)
- Capability level (basic vs frontier)
- Model strengths (code generation, reasoning, speed)

This prevents informed routing decisions: the Orchestrator cannot optimize delegation by picking a cheaper agent when multiple agents could handle a task, and cannot choose the right model when creating dynamic agents via `create_agent`.

Phase 3.3 of ADR 017 ("Cost/capability routing — Orchestrator uses model metadata from registry to optimize cost vs quality") was deferred. This ADR implements it.

## Decision

### 1. ModelCatalog — new core component

A new `ModelCatalog` class provides metadata lookup for models. It is separate from `ModelRouter` (SRP: Router resolves SDK Model instances, Catalog provides discovery metadata).

**Location:** `core/llm/catalog.py`

**Type definitions (strict Literal enums):**

- `CostTier = Literal["free", "low", "medium", "high"]`
- `CapabilityTier = Literal["basic", "standard", "advanced", "frontier"]`

Strict types eliminate ambiguity for the Orchestrator (no "cheap" vs "low" vs "inexpensive") and enable validation of user YAML overrides.

**ModelInfo dataclass:**

```python
@dataclass(frozen=True)
class ModelInfo:
    id: str                         # model name (e.g. "gpt-5-mini")
    cost_tier: CostTier
    capability_tier: CapabilityTier
    strengths: list[str]
    context_window: int | None = None
```

**ModelCatalog API:**

- `__init__(overrides: dict[str, Any] | None)` — builds catalog from settings only. Validates tier values; raises `ValueError` on unknown values.
- `get_info(model_name: str) -> ModelInfo | None` — returns `ModelInfo` with default (medium/standard) for any model; `None` only for empty string.
- `list_models() -> list[ModelInfo]` — returns explicitly configured models from settings, sorted by id.

**Default fallback** — no hardcoded model list. Any unknown model receives `cost_tier: medium`, `capability_tier: standard`, empty `strengths`, `context_window: null`. This supports any model (local, hosted, future) without catalog updates.

**Settings** — optional `models` section in `config/settings.yaml` to register explicit metadata:

```yaml
models:
  my-local-model:
    cost_tier: free
    capability_tier: basic
    strengths: [speed, privacy]
    context_window: 32000
```

Invalid tier values are rejected at startup with a clear error.

### 2. Enriched AgentInfo

`list_agents` returns model metadata alongside agent info. `AgentInfo` gains:

- `cost_tier: CostTier | None`
- `capability_tier: CapabilityTier | None`
- `strengths: list[str]`

The `_record_to_agent_info` function accepts `ModelCatalog` and looks up metadata by `record.model`; if found, populates the new fields.

### 3. list_models tool

New tool for `create_agent` workflows: returns models from the catalog with cost/capability metadata so the Orchestrator can pick the right model when creating dynamic agents.

### 4. Orchestrator prompt update

Add cost-optimization guidance to `prompts/orchestrator.jinja2`:

- Prefer agents with `cost_tier=low` for routine/simple tasks.
- Prefer higher `capability_tier` for complex tasks (code generation, multi-step reasoning).
- Use `list_models` when creating dynamic agents to pick the cheapest model that meets requirements.
- Default to lower-cost models; escalate only when the task demands it.

### 5. Wiring

- `core/runner.py` instantiates `ModelCatalog` from `settings.get("models")` and passes it to `make_delegation_tools`.
- `make_delegation_tools` gains `catalog: ModelCatalog | None = None`. When provided: (a) `list_agents` enriches `AgentInfo` with model metadata; (b) `list_models` tool is added.

## Consequences

### Positive

- Orchestrator can make cost-aware delegation decisions without programmatic routing rules.
- Fixed vocabulary (`free`/`low`/`medium`/`high`, `basic`/`standard`/`advanced`/`frontier`) prevents LLM confusion from synonyms.
- User YAML overrides are validated at startup; typos fail fast.
- Users with custom providers (LM Studio, OpenRouter, Anthropic) can register models via `models` section.
- `create_agent` model selection is informed by `list_models` instead of guessing.

### Trade-offs

- Without explicit `models` config, all models appear as medium/standard; users who want cost-aware routing register metadata in settings.
- No automatic cost tracking or budgeting; the Orchestrator follows prompt guidance only.
- `strengths` remains a free-form list (no strict enum) to allow flexibility (e.g. "code", "privacy", "multilingual").

### Migration

- No breaking changes. Existing agents work unchanged. `list_agents` gains optional fields; callers that ignore them continue to work.
- `config/settings.example.yaml` gains an example `models` section for documentation.

## References

- ADR 017: Agent Registry and Dynamic Delegation — Phase 3.3 (cost/capability routing)
- ADR 003: Agent-as-Extension
