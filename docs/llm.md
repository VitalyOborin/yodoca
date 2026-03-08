# LLM and Model Routing

The **ModelRouter** resolves agent identifiers to SDK-compatible Model instances. Configuration lives in `config/settings.yaml`; extensions can register additional agent configs via manifest `agent_config`.

---

## Overview

| Component | Location | Role |
|-----------|----------|------|
| **ModelRouter** | `core/llm/router.py` | Resolves `agent_id` → Model instance |
| **ModelCatalog** | `core/llm/catalog.py` | Model metadata (cost, capability, strengths) |
| **Providers** | `core/llm/providers/` | OpenAI-compatible (Responses or Chat Completions via `api_mode`), Anthropic |
| **Settings** | `config/settings.yaml` | `agents`, `providers`, `models` sections |

---

## Configuration Structure

### providers

Defines API endpoints and credentials. Each provider has an `id` (key in YAML) and a `type`.

```yaml
providers:
  openai:
    type: openai_compatible
    api_key_secret: OPENAI_API_KEY
    # base_url omitted => https://api.openai.com/v1

  lm_studio:
    type: openai_compatible
    base_url: http://127.0.0.1:1234/v1
    api_key_literal: lm-studio
    api_mode: chat_completions
    supports_hosted_tools: false

  anthropic:
    type: anthropic
    api_key_secret: ANTHROPIC_API_KEY

  zai:
    type: openai_compatible
    base_url: https://api.z.ai/api/paas/v4
    api_key_secret: ZAI_API_KEY
    api_mode: chat_completions
    supports_hosted_tools: false
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | str | `openai_compatible` or `anthropic` |
| `base_url` | str | API base URL (omit for OpenAI default) |
| `api_mode` | str | `responses` (default) or `chat_completions` — for `openai_compatible` only |
| `api_key_secret` | str | Env var name for API key |
| `api_key_literal` | str | Literal key (for local/dev) |
| `default_headers` | dict | Extra HTTP headers |
| `supports_hosted_tools` | bool | Whether provider supports OpenAI hosted tools (default true) |

### agents

Maps agent IDs to provider + model. The `default` entry is used when an agent has no explicit config.

```yaml
agents:
  default:
    provider: openai
    model: gpt-5-mini
  orchestrator:
    provider: openai
    model: gpt-5.2
    instructions: sandbox/prompts/orchestrator.jinja2
```

| Field | Type | Description |
|-------|------|-------------|
| `provider` | str | Provider ID from `providers` |
| `model` | str | Model identifier (e.g. `gpt-5.2`, `claude-3-5-sonnet`) |
| `instructions` | str | Path to prompt file or literal string |
| `temperature` | float | Optional (default 0.7) |
| `max_tokens` | int | Optional |

---

## Provider Types

### openai_compatible

Works with OpenAI API and compatible endpoints (OpenAI, OpenRouter, LM Studio, Ollama, etc.).

- **base_url:** Override for non-OpenAI endpoints
- **api_mode:** `responses` (default) — Responses API; `chat_completions` — Chat Completions API for providers that don't support Responses
- **api_key_secret:** Env var (e.g. `OPENAI_API_KEY`, `OPENROUTER_API_KEY`)
- **api_key_literal:** For local endpoints that accept any key
- **supports_hosted_tools:** Set `false` for providers that don't support OpenAI tool schemas (e.g. some local models)

### anthropic

Claude models via Anthropic API.

- **api_key_secret:** `ANTHROPIC_API_KEY`

---

## Extension Registration

Extensions with `agent_config` in manifest register their agent IDs with ModelRouter during `initialize_all`:

```yaml
# sandbox/extensions/builder_agent/manifest.yaml
agent_config:
  builder:
    provider: openai
    model: gpt-5
```

Then `model_router.get_model("builder")` resolves correctly.

---

## API

### get_model(agent_id: str) → Model

Returns a cached or newly built Model instance. Raises `KeyError` if no config for `agent_id` and no `default`.

### register_agent_config(agent_id, config)

Register agent config from extension manifest. Called by Loader.

### get_capability(cap, provider_id=None) → T | None

Return a capability instance from a provider that supports it. Extensions use this for provider-agnostic features (e.g. embeddings via `EmbeddingCapability`). If `provider_id` is None, returns the first provider that supports the capability.

### supports_hosted_tools(agent_id) → bool

Whether the provider for this agent supports OpenAI hosted tool types (e.g. `web_search`). Used to decide whether to add `WebSearchTool` to the Orchestrator.

### invalidate(agent_id=None)

Clear model cache after config change (hot-reload).

### health_check_all() → dict[str, bool]

Check each configured provider; return `provider_id → ok`.

---

## ModelCatalog (Cost/Capability Routing)

The **ModelCatalog** (`core/llm/catalog.py`) provides structured metadata about models for cost-aware delegation decisions. Separate from ModelRouter (SRP: Router resolves SDK instances, Catalog provides discovery metadata).

### Type definitions

Strict `Literal` types prevent synonym confusion for the Orchestrator:

- `CostTier = Literal["free", "low", "medium", "high"]`
- `CapabilityTier = Literal["basic", "standard", "advanced", "frontier"]`

### ModelInfo

```python
@dataclass(frozen=True)
class ModelInfo:
    id: str
    cost_tier: CostTier
    capability_tier: CapabilityTier
    strengths: tuple[str, ...]
    context_window: int | None = None
```

### Default fallback

There are no hardcoded model descriptions. Any model not listed in `settings.yaml` receives a neutral default: `cost_tier: medium`, `capability_tier: standard`, empty `strengths`, `context_window: null`. This lets you use any model (local, hosted, future releases) without catalog updates.

### Settings (`models` section)

Register explicit metadata for models you use:

```yaml
models:
  # cost_tier: free | low | medium | high
  # capability_tier: basic | standard | advanced | frontier
  my-local-model:
    cost_tier: free
    capability_tier: basic
    strengths: [speed, privacy]
    context_window: 32000
```

Invalid tier values are rejected at startup with a clear error.

### API

| Method | Description |
|--------|-------------|
| `get_info(model_name)` | Returns `ModelInfo` with default (medium/standard) for any model; `None` only for empty string |
| `list_models()` | Returns explicitly configured models from settings, sorted by id (empty if none) |

### Integration with delegation tools

The `list_agents` tool enriches each agent's metadata with `cost_tier`, `capability_tier`, and `strengths` from the catalog (or default for unknown models). The `list_models` tool returns the configured catalog for `create_agent` model selection.

See [ADR 019](adr/019-cost-capability-routing.md).

---

## References

- [configuration.md](configuration.md) — Full settings reference
- [extensions.md](extensions.md) — Agent extensions and `agent_config`
- [ADR 019](adr/019-cost-capability-routing.md) — Cost/Capability Routing
