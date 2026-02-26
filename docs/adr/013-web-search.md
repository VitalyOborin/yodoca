# ADR 013: Web Search Extension

## Status

Approved.

## Context

The application currently provides web search capability through OpenAI's hosted `WebSearchTool`, imported directly in the Orchestrator (`core/agents/orchestrator.py`) and `CoreToolsProvider` (`core/tools/provider.py`). This tool is conditionally included only when the active model provider reports `supports_hosted_tools == True`. In practice, this means web search is available exclusively for OpenAI models using the Responses API.

This creates several architectural problems:

1. **Provider lock-in.** Agents running on non-OpenAI providers (local models via LM Studio, Anthropic, Google, etc.) have no web search capability at all.
2. **Core depends on SDK specifics.** The core imports `WebSearchTool` from the `agents` SDK — a hosted-tool concept that is meaningless outside OpenAI's ecosystem. This violates the project principle that core must not depend on provider-specific features.
3. **No control over search behavior.** The hosted tool is a black box: the application cannot choose search engines, restrict domains by policy, control token budgets, or cache results.
4. **No page reading.** OpenAI's hosted web search performs both search and page reading internally, but the agent cannot explicitly read a specific URL. For non-OpenAI providers, the agent has no way to fetch and read web page content at all.

### The two-task nature of web search

"Web search" for an AI agent is not a single operation. It decomposes into two fundamentally different tasks:

- **Search**: accept a text query, return a ranked list of URLs with titles and snippets. The search engine handles crawling, indexing, and ranking.
- **Read (Extract)**: accept a specific URL, fetch the page (rendering JavaScript/SPA if needed), strip navigation/ads/scripts, and return clean Markdown text suitable for an LLM context window.

These tasks have different providers, different failure modes, and different cost profiles. Some providers (e.g. Tavily) handle both; others specialize in one (DuckDuckGo for search, Jina Reader for extraction). The architecture must accommodate this diversity.

### Available provider landscape

**Search providers:**

| Provider | Type | API key | Notes |
|---|---|---|---|
| DuckDuckGo (`ddgs`) | Python library, metasearch (DDG and others) | No | Free, no registration. Unreliable under rate limiting. Good as a fallback. |
| Tavily | Cloud API for LLM agents | Yes (free tier) | SOTA for agent search. Returns search results and can extract page content. |
| SearXNG | Self-hosted meta-search | No (self-hosted) | Aggregates Google, Bing, DDG. Requires Docker container. Unlimited. |
| Perplexity | Cloud Search API | Yes | Real-time ranked results, domain/language/recency filters. Separate from Sonar chat models. |

**Read/extraction providers:**

| Provider | Type | API key | Notes |
|---|---|---|---|
| Jina Reader (`r.jina.ai`) | Cloud API | No (free tier) | Renders JS, returns clean Markdown. Simple GET request. |
| Crawl4AI | Python library, local headless Chromium | No | Full SPA rendering, local execution. Heavy dependency (browser binaries). |
| Tavily Extract | Cloud API | Yes (same key as search) | Extracts content from URLs. Combined with Tavily Search. |

## Decision

### 1. New extension: `web_search`

Create a new extension at `sandbox/extensions/web_search/` implementing `ToolProvider`. The extension exposes two tools to the agent and delegates actual work to configurable provider implementations behind two internal interfaces.

### 2. Two provider interfaces

The extension defines two `Protocol` classes representing the two fundamental capabilities:

```python
class SearchProvider(Protocol):
    """Accepts a text query, returns ranked search results."""

    async def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        ...

class ReadProvider(Protocol):
    """Accepts a URL, returns extracted page content as Markdown."""

    async def read_url(self, url: str) -> ReadResult:
        ...
```

Data models:

```python
class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str

class ReadResult(BaseModel):
    url: str
    title: str
    content: str
    content_length: int
    original_length: int | None = None
    truncated: bool = False
    success: bool
    error: str = ""
```

`content_length` and `original_length` let the agent know whether it is working with a complete page or a truncated excerpt. Without this signal the model may confidently answer based on incomplete content.

A provider class may implement one interface, the other, or both (e.g. `TavilyProvider` implements both `SearchProvider` and `ReadProvider`).

### 3. Two agent tools

The extension registers two tools via `get_tools()`, mirroring the two-step workflow that OpenAI's hosted web search performs internally (`search` → `open_page`):

**Tool 1: `web_search`**

The agent calls this tool to find relevant pages for a query.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query` | `str` | Yes | Search query text |
| `limit` | `int` | No | Maximum number of results to return. Default: 5. |
| `allowed_domains` | `list[str]` | No | Restrict results to these domains (e.g. `["wikipedia.org", "github.com"]`). Empty = no restriction. |
| `time_range` | `str` | No | Filter by recency: `d` (day), `w` (week), `m` (month), `y` (year), `all`. Default: `all`. |

Returns a structured result with a list of search results (title, URL, snippet).

**Tool 2: `open_page`**

The agent calls this tool when snippets from `web_search` are insufficient and it needs the full page content.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `url` | `str` | Yes | URL of the page to read |
| `max_length` | `int` | No | Override `max_page_length` for this call. Useful when the agent needs a shorter excerpt to save context budget. |

Returns a structured result with page title and Markdown content (truncated to `max_page_length` or the per-call `max_length` override). Includes truncation metadata so the agent knows whether it received the full page.

Splitting into two tools improves LLM reasoning: the model first searches, evaluates snippet relevance, and then selectively reads only the pages it needs — saving tokens and reducing noise.

### 4. Provider selection via manifest config

The manifest declares which provider to use for each capability:

```yaml
id: web_search
name: Web Search & Reader
version: "1.0.0"
entrypoint: main:WebSearchExtension

description: |
  Searches the internet and reads web pages.
  Provides web_search and open_page tools.

enabled: true

config:
  search_provider: duckduckgo    # duckduckgo | tavily | searxng | perplexity
  read_provider: jina            # jina | tavily | crawl4ai
  max_page_length: 15000
```

The extension reads `search_provider` and `read_provider` from config during `initialize()` and instantiates the corresponding classes. If a hybrid provider (e.g. Tavily) is selected for both roles, a single instance is shared.

### 5. Provider factory

The extension's `initialize()` method acts as a simple factory:

```python
async def initialize(self, context: ExtensionContext) -> None:
    search_name = context.get_config("search_provider", "duckduckgo")
    read_name = context.get_config("read_provider", "jina")
    max_page_length = context.get_config("max_page_length", 15000)

    self._searcher = await self._create_search_provider(search_name, context)
    self._reader = await self._create_read_provider(read_name, context)
```

Provider constructors receive the `ExtensionContext` for secret access. `ExtensionContext.get_secret()` is already implemented (ADR 012, Phase 1) — it reads from the OS keyring with `.env` fallback. For example, Tavily's API key is retrieved via `await context.get_secret("TAVILY_API_KEY")`. Phase 2 adds `SetupProvider` to the extension so that the onboarding flow can collect API keys interactively when a premium provider is selected.

### 6. File structure and naming convention

```
sandbox/extensions/web_search/
├── manifest.yaml
├── main.py                  # WebSearchExtension: lifecycle, ToolProvider, factory
├── interfaces.py            # SearchProvider, ReadProvider protocols + Pydantic models
└── providers/
    ├── __init__.py
    ├── duckduckgo.py        # DuckDuckGoSearchProvider (SearchProvider)
    ├── jina.py              # JinaReadProvider (ReadProvider)
    ├── perplexity.py        # PerplexitySearchProvider (SearchProvider)
    ├── tavily.py            # TavilyProvider (SearchProvider + ReadProvider)
    └── searxng.py           # SearXngSearchProvider (SearchProvider)
```

Providers live in a single `providers/` directory (not split into `search_providers/` and `read_providers/`) because hybrid providers implement both interfaces. The naming convention enforces clarity: classes are suffixed `*SearchProvider` or `*ReadProvider` (or both words for hybrids that implement both protocols). File names match the external service name (`duckduckgo.py`, `jina.py`, `tavily.py`).

### 7. Tool output contracts

Both tools return structured Pydantic models (per project convention: all agent tools return structured output).

**`web_search` output:**

```python
class WebSearchToolResult(BaseModel):
    results: list[SearchResultItem]
    message: str = ""

class SearchResultItem(BaseModel):
    title: str
    url: str
    snippet: str
```

**`open_page` output:**

```python
class OpenPageToolResult(BaseModel):
    url: str
    title: str
    content: str
    content_length: int
    original_length: int | None = None
    truncated: bool = False
    status: Literal["success", "error"]
    error: str = ""
```

The `truncated` flag and length fields mirror `ReadResult` from the provider layer. The agent sees these in the tool output and can decide whether to refine its query or ask the user for clarification when working with partial content.

### 8. Removing WebSearchTool from core

After the extension is implemented and validated:

1. Remove `WebSearchTool` import and usage from `core/agents/orchestrator.py` (line 6, lines 51-52).
2. Remove `WebSearchTool` import and usage from `core/tools/provider.py` (lines 33-34).
3. The `supports_hosted_tools()` method on `ModelRouter` remains (other hosted tools may use it in the future), but web search no longer depends on it.

The extension fully replaces the core-level web search. All model providers — OpenAI, local, Anthropic — get consistent web search behavior.

**Backwards compatibility.** The extension's first tool is registered as `web_search` — the same name the OpenAI hosted tool used. Declarative agents or prompts that reference "web_search" by name will resolve to the extension tool transparently. No aliases or shims are required. The only behavioral difference is that the extension returns structured `WebSearchToolResult` (list of results) instead of the opaque hosted-tool output, which is a strict improvement for non-OpenAI providers and equivalent for OpenAI providers.

### 9. Phased implementation

**Phase 1 (MVP):**
- `DuckDuckGoSearchProvider` — free, no API key, works out of the box.
- `JinaReadProvider` — free, no API key, handles SPA rendering server-side.
- Two tools registered via `ToolProvider.get_tools()`.
- Remove `WebSearchTool` from core.

**Phase 2 (Premium providers):**
- `TavilyProvider` — higher quality search and extraction, requires API key.
- `SearXngSearchProvider` — self-hosted meta-search for privacy-focused setups.
- `SetupProvider` implementation for API key onboarding (Tavily).

**Phase 3 (Advanced features):**
- `Crawl4AIReadProvider` — local headless browser extraction (no external dependency on Jina).
- Content caching to avoid re-fetching the same URL within a session.
- Domain allowlist/blocklist configuration.
- Citation support: `citation_id` in search results, system prompt instruction for inline citations, post-processing of `[N]` markers before delivery to user.

### 10. Citation architecture (Phase 3)

OpenAI's hosted web search provides inline citations with `url_citation` annotations. To replicate this for all providers without introducing cross-extension coordination:

1. Each `SearchResultItem` includes a `citation_id: int` field, assigned sequentially within a tool call.
2. The `web_search` tool result includes a `sources` list that maps `citation_id` → URL + title.
3. The extension adds a system-prompt instruction: *"When stating facts from web search results, append the citation ID in brackets, e.g. [1]."*
4. Sources live entirely within the tool result — no shared state between extensions. The agent's final response naturally contains `[N]` markers because the LLM follows the instruction.
5. Post-processing (replacing `[N]` with clickable links, appending a "Sources" footer) is handled at the channel/output layer as a formatting concern, not by the `web_search` extension itself. Channels that support rich formatting (Telegram, Web UI) can parse `[N]` markers; plain-text channels pass them through as-is.

This approach keeps citation state inside tool results and avoids cross-extension dependencies (no Event Bus subscriptions, no shared `session_sources` object). It is deferred to Phase 3 because it requires system-prompt injection and channel-side formatting, neither of which is critical for the MVP.

## Consequences

### Benefits

1. **Provider-agnostic web search.** Any model provider gets web search — no more OpenAI lock-in. DuckDuckGo + Jina work without any API keys.
2. **Separation of concerns.** Search and read are separate interfaces with independent providers. Adding a new search engine (e.g. Perplexity, Brave Search) requires only a new file in `providers/` — no changes to tool logic or agent configuration.
3. **Token economy.** Raw HTML of a modern page is 100-500 KB. Jina/Tavily extraction returns 5-20 KB of clean Markdown — an 80-95% reduction. The two-tool design means the agent only reads pages when snippets are insufficient.
4. **Better LLM reasoning.** Two distinct tools (`web_search` and `open_page`) mirror how humans interact with the web: search first, then read selectively. This improves the model's decision-making about when to search vs. when to read.
5. **Core simplification.** Removing `WebSearchTool` from `orchestrator.py` and `CoreToolsProvider` eliminates a provider-specific dependency from core, aligning with the project principle that core must not depend on extensions or provider SDKs.
6. **Extensibility.** The `SearchProvider`/`ReadProvider` protocol pair can accommodate future providers (Brave Search, Perplexity, Firecrawl, local Crawl4AI) without architectural changes.

### Trade-offs

| Trade-off | Impact |
|---|---|
| **DuckDuckGo is unreliable** | DDG frequently rate-limits automated requests. Acceptable for Phase 1 MVP; Tavily (Phase 2) or SearXNG provide production-grade alternatives. |
| **Jina Reader is an external dependency** | `r.jina.ai` is a free public service with no SLA. If it goes down, `open_page` fails. Mitigation: Tavily Extract or Crawl4AI as alternative ReadProviders. |
| **Two tools instead of one** | Adds cognitive load for the LLM (two tool calls vs. one). In practice, this improves accuracy because the model can evaluate snippets before committing to a full page read. |
| **No built-in citations in Phase 1** | Phase 1 returns plain text without structured citation annotations. The model may still cite sources naturally, but there is no guaranteed citation format until Phase 3. |
| **Loss of OpenAI's hosted search quality** | The hosted `WebSearchTool` benefits from OpenAI's proprietary ranking and integration with reasoning models. Third-party providers may return lower-quality results for some queries. |

### Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **DuckDuckGo IP ban** | Medium | Rate limiting in provider, configurable delay between requests. Tavily as upgrade path. |
| **DuckDuckGo ToS / compliance** | Medium | `ddgs` is an unofficial metasearch library, not a sanctioned API. DDG may change HTML structure or block scraping at any time. Acceptable for a free-tier fallback; should not be the only provider in production. |
| **Jina service outage** | Low | Graceful error in `ReadResult.error`; agent informed, can retry or skip. |
| **Page content exceeds context window** | Medium | `max_page_length` config truncates content. Default 15,000 chars. `truncated` flag in result informs the agent. |
| **SSRF via `open_page`** | Medium | The LLM controls the `url` parameter. Without validation it could target `http://localhost`, `http://169.254.169.254` (cloud metadata), `file://`, or internal network addresses — leaking local data or environment metadata. Mitigation: URL validation in `main.py` before dispatching to any `ReadProvider` — reject non-HTTP(S) schemes, resolve DNS and reject private/loopback IP ranges (RFC 1918, link-local, localhost). Applies uniformly to all providers. |
| **Malicious URL — other vectors** | Low | Beyond SSRF, the tool only reads; no code execution. Provider handles rendering safely (server-side for Jina, sandboxed browser for Crawl4AI). |
| **Provider API key leakage** | Low | Keys stored via `context.get_secret()` (keyring/env), never in manifest or logs. See ADR 012. |

## Relation to Other ADRs

- **ADR 002** — Extension architecture: `web_search` is a standard `ToolProvider` extension, following the established extension contract and lifecycle.
- **ADR 003** — Agent as extension: the extension provides tools to the Orchestrator agent, same as other ToolProvider extensions (shell_exec, memory, etc.).
- **ADR 012** — Secrets: API keys for premium providers (Tavily) are stored and retrieved via `context.get_secret()`, following the secrets management pattern.
