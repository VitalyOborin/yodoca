# ADR 026: Web Channel — HTTP API Extension for Frontend Applications

## Status

Proposed

## Context

The system currently supports two user-facing channels: CLI (stdin/stdout) and Telegram
(Bot API). Both are transport-specific adapters following the `ChannelProvider` protocol.
There is no HTTP-based interface that would allow a **web frontend application** to
interact with the system.

Several mature open-source chat frontends exist — LibreChat, LobeChat, Open WebUI,
ChatBox, and others. They connect to any backend that implements the OpenAI API format.
Providing an OpenAI-compatible HTTP API would allow users to plug any such frontend into
the system without building a custom UI.

Beyond the chat interface, a web frontend also needs access to application-level data:
inbox items, memory, agent configuration, prompts. These require custom REST endpoints
that go beyond what the OpenAI API specification covers.

Requirements:

- **OpenAI Chat Completions API** (`GET /v1/models`, `POST /v1/chat/completions`) —
  enables third-party OpenAI-compatible frontends.
- **OpenAI Responses API** (`POST /v1/responses`) — the newer OpenAI API format with
  richer event semantics and structured output items.
- **Streaming** — both APIs must support Server-Sent Events (SSE) for incremental
  response delivery, matching the OpenAI streaming format.
- **Custom application API** — REST endpoints for inbox, memory, agents, prompts, and
  system health. Basic functionality first, expanding over time.
- **Minimal core impact** — the extension must integrate through existing protocols
  and `ExtensionContext` API; any core changes should be backward-compatible.

Constraints:

- local single-user runtime (no multi-tenant, no OAuth flows);
- preserve extension architecture and contracts;
- `core/` must remain independent from the extension;
- must coexist with CLI and Telegram channels.

## Decision

### 1) Extension identity and protocols

Create extension `sandbox/extensions/web_channel/` implementing:

- **`ChannelProvider`** — reactive (`send_to_user`) and proactive (`send_message`)
  delivery, following the same contract as CLI and Telegram channels.
- **`StreamingChannelProvider`** — incremental response delivery
  (`on_stream_start`, `on_stream_chunk`, `on_stream_status`, `on_stream_end`).
- **`ServiceProvider`** — runs the HTTP server as a background task via
  `run_background()`.

The extension ID is `web_channel`, following the `*_channel` naming convention
(`cli_channel`, `telegram_channel`, `web_channel`). The "web" qualifier describes
the transport (HTTP/SSE), not a bundled UI — the frontend is external.

### 2) HTTP framework and server

**FastAPI** on **uvicorn** (ASGI):

| Criterion | FastAPI + uvicorn |
|-----------|-------------------|
| Pydantic integration | Native; consistent with project conventions |
| OpenAPI docs | Auto-generated; useful during frontend development |
| SSE / streaming | `StreamingResponse` (Starlette) or `sse-starlette` |
| Async support | Full asyncio; integrates with the existing event loop |
| Ecosystem | Widely adopted, well-documented |

Uvicorn runs inside `run_background()` with `loop="none"` to reuse the existing asyncio
event loop:

```python
async def run_background(self) -> None:
    config = uvicorn.Config(self._app, host=host, port=port, loop="none", log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
```

The Loader wraps `run_background()` in `asyncio.create_task()` and cancels on shutdown,
which triggers uvicorn's graceful shutdown.

### 3) OpenAI Chat Completions API

#### GET /v1/models

Returns the list of models available in the system. Each model entry corresponds to
an agent-level model from the configuration, with the primary entry being the
Orchestrator's model (the system's "default" model from the external client's
perspective).

Response format follows the OpenAI specification:

```json
{
  "object": "list",
  "data": [
    {
      "id": "yodoca",
      "object": "model",
      "created": 1709000000,
      "owned_by": "yodoca"
    }
  ]
}
```

The simplest approach: return a single virtual model `"yodoca"` that routes to the
Orchestrator. Optionally, expose additional models from `config/settings.yaml` if the
frontend needs model selection. The `model` field in incoming requests is accepted
but does not change routing in Phase 1 — all requests go through the Orchestrator
pipeline.

#### POST /v1/chat/completions

Accepts the standard Chat Completions request body:

```json
{
  "model": "yodoca",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "Hello"}
  ],
  "stream": false,
  "temperature": 0.7
}
```

Processing:

1. Extract the **last user message** from the `messages` array. The system maintains
   its own conversation context (SQLiteSession + ContextProviders), so the full
   messages history sent by the client is acknowledged but not forwarded verbatim
   to the model.
2. Route through the standard channel pipeline: emit `user.message` event → kernel
   handler → `router.handle_user_message()` → Orchestrator → response.
3. Format the response in Chat Completions format.

**Non-streaming response:**

```json
{
  "id": "chatcmpl-<uuid>",
  "object": "chat.completion",
  "created": 1709000000,
  "model": "yodoca",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "Hello! How can I help?"},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

**Streaming response** (`"stream": true`): SSE with `text/event-stream` content type.
See §6 for streaming architecture.

### 4) OpenAI Responses API

#### POST /v1/responses

The Responses API is the newer OpenAI format with richer event semantics.

Request body:

```json
{
  "model": "yodoca",
  "input": [
    {"role": "user", "content": "Hello"}
  ],
  "stream": false
}
```

The `input` field accepts both a **string** and an **array of message objects** per
the OpenAI specification. When `input` is a plain string, it is treated as the user
message directly. When it is an array, the last user message is extracted (same logic
as Chat Completions §3). The Pydantic request model must use `str | list[dict]` to
avoid returning 422 for the string variant, which some SDKs generate.

Processing follows the same pipeline as Chat Completions (§3): extract the last user
input, route through the channel mechanism, format the response.

**Non-streaming response:**

```json
{
  "id": "resp_<uuid>",
  "object": "response",
  "status": "completed",
  "output": [
    {
      "id": "msg_<uuid>",
      "type": "message",
      "role": "assistant",
      "content": [
        {"type": "output_text", "text": "Hello! How can I help?"}
      ]
    }
  ],
  "model": "yodoca",
  "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
}
```

**Streaming response** (`"stream": true`): SSE with Responses API event types
(`response.created`, `response.output_text.delta`, `response.completed`, etc.).
See §6.

### 5) Request-response bridging

The ChannelProvider protocol is callback-based: the kernel calls `send_to_user()` or
streaming methods on the channel when the response is ready. HTTP, however, requires
request-response correlation — the handler that received the HTTP request must obtain
the agent's response and return it.

The web channel bridges these two models using **per-request async primitives**
(asyncio.Future for non-streaming, asyncio.Queue for streaming).

**Stable `user_id` vs internal `request_id`.** The `user.message` event carries a
**stable `user_id`** — `"web_user"` by default (configurable via
`config.default_user_id`). This ensures that memory episode ingestion, logging,
ContextProviders (`TurnContext.user_id`), and all other subscribers that key on
`user_id` see a consistent identity across requests.

Request-response correlation is handled internally by the extension using a separate
mechanism: since the MessageRouter lock serializes agent invocations, only one request
is active at a time. The extension tracks the active request's Future or Queue and
routes `send_to_user("web_user", response)` to it. The `request_id` never leaves the
extension boundary and never appears in event payloads.

#### Non-streaming flow

```
HTTP POST /v1/chat/completions (stream=false)
  │
  ├─ create asyncio.Future, store as self._active_future
  ├─ emit "user.message" event {text, user_id="web_user", channel_id="web_channel"}
  │
  │  ← EventBus dispatches to kernel handler
  │  ← router.handle_user_message() invokes Orchestrator
  │  ← router calls channel.send_to_user("web_user", response)
  │
  ├─ send_to_user("web_user", response):
  │     self._active_future.set_result(response)
  │
  ├─ response = await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT)
  └─ return formatted Chat Completion JSON
```

#### Streaming flow

```
HTTP POST /v1/chat/completions (stream=true)
  │
  ├─ create asyncio.Queue, store as self._active_stream
  ├─ emit "user.message" event {text, user_id="web_user", channel_id="web_channel"}
  ├─ return StreamingResponse(event_generator())
  │
  │  event_generator() reads from queue:
  │    while True:
  │      event = await queue.get()
  │      if event is sentinel → yield "data: [DONE]\n\n", break
  │      yield format_sse_chunk(event)
  │
  │  Meanwhile, kernel calls streaming callbacks:
  │    on_stream_start("web_user") → queue.put(start_event)
  │    on_stream_chunk("web_user", chunk) → queue.put(chunk_event)
  │    on_stream_status("web_user", status) → queue.put(status_event)
  │    on_stream_end("web_user", full_text) → queue.put(sentinel)
  │
  └─ SSE stream closes after sentinel
```

This pattern keeps the web channel compliant with the ChannelProvider protocol —
the kernel treats it identically to CLI or Telegram. The request-response bridging
is entirely internal to the extension.

**Concurrency and busy-rejection.** The `asyncio.Lock` in MessageRouter serializes
agent invocations. Since only one request is active at a time, `_active_future` /
`_active_stream` is always unambiguous.

Without protection, a second HTTP request arriving while the first is being processed
would emit `user.message` → EventBus accepts it → kernel handler awaits the Lock →
the SSE generator (or Future) hangs on an empty queue with no events flowing — the
client sees a silently stuck request until the 120-second timeout.

The web channel prevents this with an **extension-level busy guard**: an
`asyncio.Lock` (or a simple boolean flag) acquired in the HTTP handler **before**
emitting `user.message`. If the guard is already held, the handler returns
immediately:

```
HTTP 503 Service Unavailable
Retry-After: 5
{"error": {"message": "Another request is being processed. Retry shortly.",
           "type": "server_error", "code": "busy"}}
```

This gives the client an actionable signal. Well-behaved frontends (LibreChat,
Open WebUI) respect `Retry-After` or surface the error to the user.

**Guard release timing.** The guard is released by the ChannelProvider callbacks:
`send_to_user()` (non-streaming) or `on_stream_end()` (streaming) — not by the
SSE generator's `finally` block. This is critical: if the client disconnects
mid-stream, the SSE generator exits its loop, but the agent invocation continues
to completion under the MessageRouter lock. `on_stream_end()` will still be called
by the kernel when the agent finishes, and that is where the guard is released.
Releasing it earlier (e.g., in the generator's `finally`) would allow a new request
to enter while the Orchestrator is still running, corrupting `_active_stream` state.
A safety timeout (equal to `request_timeout_seconds`) ensures the guard is released
even if the kernel callback never arrives due to an unexpected failure.

If concurrency support is added later, the extension will switch to a per-request
correlation map keyed by a composite of `user_id` + `request_id`, and the busy
guard will be replaced by a concurrent-request limiter.

### 6) Streaming via SSE

Both OpenAI APIs use Server-Sent Events for streaming. The extension translates
`StreamingChannelProvider` callbacks into the appropriate SSE format.

#### Chat Completions streaming format

```
data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":1709000000,"model":"yodoca","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":1709000000,"model":"yodoca","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":1709000000,"model":"yodoca","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Mapping from StreamingChannelProvider callbacks:

| Callback | SSE event |
|----------|-----------|
| `on_stream_start` | First chunk with `delta.role = "assistant"` |
| `on_stream_chunk(chunk)` | Chunk with `delta.content = chunk` |
| `on_stream_status(status)` | No direct Chat Completions equivalent; optionally emitted as a comment line (`:`-prefixed) for debugging |
| `on_stream_end(full_text)` | Final chunk with `finish_reason = "stop"`, then `data: [DONE]` |

#### Responses API streaming format

```
event: response.created
data: {"id":"resp_<uuid>","object":"response","status":"in_progress",...}

event: response.output_item.added
data: {"item":{"id":"msg_<uuid>","type":"message","role":"assistant","content":[]}}

event: response.content_part.added
data: {"item_id":"msg_<uuid>","content_index":0,"part":{"type":"output_text","text":""}}

event: response.output_text.delta
data: {"item_id":"msg_<uuid>","content_index":0,"delta":"Hello"}

event: response.output_text.done
data: {"item_id":"msg_<uuid>","content_index":0,"text":"Hello! How can I help?"}

event: response.completed
data: {"id":"resp_<uuid>","object":"response","status":"completed",...}
```

Mapping:

| Callback | SSE event(s) |
|----------|--------------|
| `on_stream_start` | `response.created` + `response.output_item.added` + `response.content_part.added` |
| `on_stream_chunk(chunk)` | `response.output_text.delta` |
| `on_stream_status(status)` | Custom event (see note below) |
| `on_stream_end(full_text)` | `response.output_text.done` + `response.completed` |

**`on_stream_status` note:** This callback delivers a plain string (e.g.,
`"Using: search_memory"`), not a structured function call object. Mapping it to the
Responses API `function_call` item type would be semantically incorrect — a real
`function_call` item requires `name`, `arguments`, and `call_id`. Instead, the
extension emits a custom event that compatible frontends can display as a status
indicator:

```
event: response.output_item.added
data: {"item": {"type": "status_update", "text": "Using: search_memory"}}
```

Frontends that do not recognize `status_update` will ignore it per SSE semantics.
Phase 2 may introduce structured tool-call streaming if the kernel exposes richer
event data from the Orchestrator pipeline.

### 7) Custom application API

Beyond the OpenAI-compatible endpoints, the extension serves custom REST endpoints
for application management. These are namespaced under `/api/` to separate them from
the OpenAI contract.

#### Phase 1 (MVP)

| Method | Endpoint | Description | Data source |
|--------|----------|-------------|-------------|
| GET | `/api/health` | System health and uptime | Extension-local |
| GET | `/api/conversations` | List recent conversation sessions | Session pool (§11) |
| DELETE | `/api/conversations/{id}` | Delete a conversation session | Session pool (§11) |

Phase 1 focuses on the minimum needed for a functional chat frontend. Conversation
management is essential for multi-session UIs. Health check is standard.

#### Phase 2

| Method | Endpoint | Description | Dependency |
|--------|----------|-------------|------------|
| GET | `/api/inbox` | List inbox items (filter by source, type, status) | `inbox` |
| GET | `/api/inbox/{id}` | Read single inbox item | `inbox` |
| GET | `/api/memory/search` | Search memory (hybrid FTS + vector) | `memory` |
| GET | `/api/agents` | List available agents with metadata | AgentRegistry |
| GET/PUT | `/api/agents/{id}` | Read/update agent configuration | filesystem + Loader |
| GET | `/api/prompts` | List system prompts | filesystem |
| GET/PUT | `/api/prompts/{name}` | Read/update prompt content | filesystem |
| GET | `/api/extensions` | List loaded extensions with status | Loader |
| GET | `/api/events` | Recent event log | EventBus |

Phase 2 endpoints that access other extensions (inbox, memory) will require adding
those IDs to `depends_on`. To avoid hard-failing when optional extensions are absent,
two approaches are viable:

- **A) Conditional registration:** During `initialize()`, check which extensions are
  available (by trying `get_extension()` for declared dependencies) and register
  only the routes for available extensions. Routes for missing dependencies return
  404 with an informative message.
- **B) Separate API extension:** Move advanced application endpoints to a dedicated
  `app_api` extension with its own `depends_on`, leaving `web_channel` focused on
  the chat channel and OpenAI compatibility.

The recommended approach (A vs B) will be decided during Phase 2 implementation.

### 8) Authentication

Single-user, API-key-based authentication:

- Clients pass the key in the `Authorization: Bearer <key>` header (matching the
  OpenAI API convention used by all compatible frontends).
- The key is configured via `config.api_key` in manifest or `settings.yaml`.
  If not set, the extension reads from `ctx.get_secret("web_channel.api_key")`.
- If no key is configured at all, authentication is **disabled** (local development
  convenience; a warning is logged at startup).
- Invalid or missing key returns HTTP 401 with a standard error body.

The authentication middleware applies to all routes (`/v1/*` and `/api/*`).

**CORS.** The middleware must explicitly allow the headers that clients send in
preflight (`OPTIONS`) requests. `cors_origins` alone is not sufficient — browsers
block the actual request if the preflight response omits
`Access-Control-Allow-Headers` for the requested headers:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Session-Id"],
)
```

Without `allow_headers`, any browser-based frontend (LibreChat, LobeChat, Open WebUI)
will fail on the first request: `OPTIONS` succeeds, but the follow-up `POST` with
`Authorization: Bearer ...` is blocked. Tools like curl/Postman skip preflight and
mask the bug.

Default `cors_origins: ["*"]` is for local development convenience. Tighter origins
should be set in production.

### 9) Session management and conversation context

#### Message history

OpenAI-compatible clients typically send the full message history in each request.
The system, however, maintains its own conversation context:

- **SQLiteSession** stores conversation history in the MessageRouter.
- **ContextProviders** (e.g., memory) inject relevant context before each invocation.

The web channel uses only the **last user message** from the incoming `messages` /
`input` array. The full message history sent by the client is not forwarded to the
model — the system's own context pipeline provides continuity. The continuity works
because SQLiteSession is per-session (not per-user), and the stable `user_id` (§5)
ensures consistent identity for memory and other subscribers.

#### Stable user identity

Every `user.message` event from the web channel carries a stable `user_id`
(default: `"web_user"`, configurable via `config.default_user_id`). This is
critical for correct behavior of:

- memory episode ingestion (keyed on `user_id`);
- per-user context in ContextProviders (`TurnContext.user_id`);
- logging and analytics.

The HTTP request-level `request_id` is used **only** for internal Future/Queue
correlation (§5) and never appears in event payloads.

Future extension: in a multi-user scenario, `user_id` could be derived from the
API key (e.g., hash of the key) to separate per-user context without additional
configuration.

#### Multi-session support via `X-Session-Id` (Phase 1)

Frontend applications (LibreChat, LobeChat, Open WebUI) manage multiple conversations
and expect the backend to support session switching from day one. Without this,
users cannot have parallel conversations or return to previous threads.

The web channel supports session switching via the `X-Session-Id` request header:

- If `X-Session-Id: <uuid>` is present, the web channel passes the session ID to
  the router, which selects (or creates) a dedicated `SQLiteSession` for that ID.
- If absent, the default (global) session is used — matching the behavior of CLI
  and Telegram channels.

This requires a **minor core enhancement** (see §11): `router.handle_user_message()`
must accept an optional `session_id` parameter. When provided, the router uses a
session pool instead of the single global session. Backward compatibility is
preserved — channels that do not pass `session_id` continue to use the default
session with no code changes.

### 10) Proactive message delivery

The `ChannelProvider` protocol requires `send_message()` for proactive notifications
(scheduled reminders, task completion, event-driven alerts). HTTP is pull-based, so
the web channel must bridge push semantics to a polling model.

**Ring buffer + long-poll endpoint.**

`send_message(text)` appends to an in-memory ring buffer
(`collections.deque(maxlen=100)`). A dedicated endpoint lets the frontend retrieve
pending notifications:

```
GET /api/notifications?timeout=30
```

Behavior:

- If the buffer contains unread messages, return them immediately and mark as
  consumed.
- If the buffer is empty, hold the connection open (long-poll) for up to `timeout`
  seconds (default 30, max 60). If a notification arrives during the wait, return
  it immediately. If the timeout expires, return an empty list with HTTP 200.
- Response format:

```json
{
  "notifications": [
    {"id": "notif_<uuid>", "text": "Reminder: standup in 5 min", "created_at": 1709000000}
  ]
}
```

The long-poll is implemented with `asyncio.Event`: `send_message()` appends to the
deque and sets the event; the HTTP handler awaits the event with
`asyncio.wait_for(event.wait(), timeout)`.

This approach fully satisfies the `send_message()` contract, requires no WebSocket
infrastructure, and handles the expected notification volume (single-user, low
frequency) with no persistence overhead.

**Phase 2 upgrade path:** Replace long-poll with a persistent SSE or WebSocket
connection for lower latency. The ring buffer and `send_message()` implementation
remain unchanged — only the delivery transport changes.

### 11) Core changes

Phase 1 requires one **minor** core enhancement for multi-session support:

| Change | Location | Description |
|--------|----------|-------------|
| Session pool | `core/extensions/router.py` | `handle_user_message()` accepts an optional `session_id` parameter. When provided, selects a `SQLiteSession` from a pool keyed by session ID instead of the single global session. Default behavior (no `session_id`) is unchanged — CLI, Telegram, and all existing channels are unaffected. |

This change is backward-compatible and benefits all channels (e.g., Telegram could
later use `chat_id` as `session_id` for per-chat history).

All other necessary `ExtensionContext` API already exists:

| Need | Existing API |
|------|--------------|
| Receive agent response | `ChannelProvider.send_to_user()` |
| Streaming response | `StreamingChannelProvider` callbacks |
| Emit user message | `ctx.emit("user.message", ...)` |
| Run HTTP server | `ServiceProvider.run_background()` |
| Read config | `ctx.get_config(key, default)` |
| Read secrets | `ctx.get_secret(name)` |
| Access other extensions | `ctx.get_extension(ext_id)` via `depends_on` |
| Proactive notifications | `ChannelProvider.send_message()` |

### 12) Configuration and manifest

```yaml
id: web_channel
name: Web Channel
version: "1.0.0"
entrypoint: main:WebChannelExtension

description: |
  HTTP API for frontend applications. Provides OpenAI-compatible Chat Completions
  and Responses API endpoints, plus custom application REST API.
  Supports streaming via Server-Sent Events (SSE).
  Compatible with LibreChat, LobeChat, Open WebUI, and other OpenAI-compatible frontends.

depends_on: []

config:
  host: "127.0.0.1"
  port: 8080
  api_key: ""
  cors_origins: ["*"]
  request_timeout_seconds: 120
  model_name: "yodoca"
  default_user_id: "web_user"

enabled: true
```

Configuration via `settings.yaml` overrides (standard priority):

```yaml
extensions:
  web_channel:
    host: "0.0.0.0"
    port: 8080
    api_key: "sk-my-secret-key"
    cors_origins: ["http://localhost:3000"]
```

### 13) New Python dependencies

| Package | Purpose | Already in project? |
|---------|---------|---------------------|
| `fastapi` | HTTP framework, request validation, OpenAPI | No |
| `uvicorn[standard]` | ASGI server | No |

`sse-starlette` is optional; SSE can be implemented with Starlette's built-in
`StreamingResponse`. Decision deferred to implementation.

### 14) File structure

```
sandbox/extensions/web_channel/
├── manifest.yaml       # Extension manifest
├── main.py             # WebChannelExtension: lifecycle, protocol delegation
├── bridge.py           # Request-response bridging: _active_future/_active_stream, busy guard, cleanup
├── app.py              # FastAPI application factory, middleware, CORS
├── routes_openai.py    # /v1/models, /v1/chat/completions, /v1/responses
├── routes_api.py       # /api/* custom application endpoints
├── models.py           # Pydantic request/response models (OpenAI format)
└── streaming.py        # SSE formatting helpers for Chat Completions and Responses
```

### 15) Phased delivery

**Phase 1 (MVP) — OpenAI-compatible chat API:**

- FastAPI + uvicorn HTTP server via `ServiceProvider`
- `ChannelProvider` + `StreamingChannelProvider` implementation
- Stable `user_id` (`"web_user"`) with internal request correlation
- `X-Session-Id` header for multi-session support
- Minor core enhancement: session pool in `MessageRouter` (§11)
- `GET /v1/models` — virtual model list
- `POST /v1/chat/completions` — non-streaming and SSE streaming
- `POST /v1/responses` — non-streaming and SSE streaming
- `GET /api/health` — basic health check
- `GET /api/conversations`, `DELETE /api/conversations/{id}` — session management
- Bearer token authentication
- CORS support
- Request-response bridging via async primitives

**Phase 2 — custom application API:**

- Inbox endpoints (`/api/inbox`, `/api/inbox/{id}`) — requires `depends_on: [inbox]`
- Memory search endpoint (`/api/memory/search`) — requires `depends_on: [memory]`
- Agent and prompt management endpoints
- Extension status endpoint
- WebSocket support for proactive notifications
- Rate limiting

## Consequences

### Positive

- Enables any OpenAI-compatible frontend (LibreChat, LobeChat, Open WebUI, ChatBox)
  to connect to the system without custom integration.
- Two API formats (Chat Completions + Responses) maximize frontend compatibility.
- SSE streaming provides real-time response delivery matching user expectations from
  modern chat interfaces.
- Follows the established channel pattern — kernel treats web_channel identically to
  CLI and Telegram; no special-casing in core.
- One minor backward-compatible core change (session pool); everything else is
  self-contained in the extension.
- FastAPI auto-generates OpenAPI documentation, easing custom frontend development.
- Coexists with CLI and Telegram channels — all three can be enabled simultaneously.

### Trade-offs

- Agent invocations are serialized by the MessageRouter lock; concurrent HTTP requests
  queue. Acceptable for single-user but limits throughput if multi-user is added later.
- The client's message history is not forwarded to the model; the system's own context
  pipeline provides continuity. This may surprise users who expect exact OpenAI behavior
  with explicit message history control.
- `usage` fields in responses (`prompt_tokens`, `completion_tokens`) are approximate
  or zero in Phase 1, since the Orchestrator pipeline (tools, context providers)
  makes token counting non-trivial.
- Adding new Python dependencies (fastapi, uvicorn) increases the project's footprint.

### Risks and mitigations

- **Port conflict**: The configured port may already be in use. `uvicorn.Server.serve()`
  raises `OSError` on bind failure. The extension catches this in `run_background()`,
  logs the error, and returns (health_check returns False → extension marked ERROR).
  Configurable port reduces risk.
- **Timeout on long agent runs**: Complex agent tasks (multi-tool, delegation) may
  exceed the HTTP timeout. The `request_timeout_seconds` config (default 120s) controls
  the `asyncio.wait_for` in the bridging layer. Streaming mitigates perceived delay
  since chunks arrive progressively.
- **SSE connection drops**: If the client disconnects mid-stream, the SSE generator
  detects the closed connection (via Starlette's disconnect detection) and cleans up
  the per-request stream state. The agent invocation continues to completion (lock
  is released normally); the response is simply not delivered.
- **Memory leak from abandoned requests**: Per-request `_active_future` /
  `_active_stream` must be cleaned up on timeout and client disconnect. A guard
  in the HTTP handler ensures cleanup on any exit path (normal, timeout, or
  exception).
