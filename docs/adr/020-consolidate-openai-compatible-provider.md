# ADR 020: Consolidate OpenAI-Compatible Provider

## Status

Accepted. Implemented

## Context

The application had two provider types for OpenAI-compatible APIs:

- `openai_compatible` — uses `OpenAIResponsesModel` (Responses API)
- `litellm_openai_compatible` — uses `LitellmModel` (Chat Completions API via LiteLLM)

Both targeted the same class of endpoints (OpenAI, OpenRouter, LM Studio, Z.ai, etc.). The distinction was only which SDK path to use: Responses API vs Chat Completions API. The OpenAI Agents SDK provides both `OpenAIResponsesModel` and `OpenAIChatCompletionsModel`, both accepting the same `AsyncOpenAI` client. LiteLLM added an optional dependency without benefits for single-endpoint OpenAI-compatible providers. Additionally, `litellm_openai_compatible` lacked embeddings capability and had a weak health check (build-only, no real API call).

## Decision

1. **Remove** `litellm_openai_compatible` as a separate provider type.
2. **Add** `api_mode: "responses" | "chat_completions"` to `openai_compatible` provider config.
3. **Switch** in `OpenAICompatibleProvider.build()`:
   - `api_mode: "responses"` (default) → `OpenAIResponsesModel`
   - `api_mode: "chat_completions"` → `OpenAIChatCompletionsModel`

`AnthropicProvider` remains unchanged — it uses LiteLLM for Anthropic's non-OpenAI API format.

## Consequences

- Simpler configuration: one provider type for all OpenAI-compatible endpoints.
- No optional `openai-agents[litellm]` extra for chat/completions-only providers.
- Health check and embeddings work correctly for all OpenAI-compatible endpoints.
- Migration: replace `type: litellm_openai_compatible` with `type: openai_compatible` and add `api_mode: chat_completions`; remove `api_base`/`litellm_model_prefix` in favor of `base_url`.
