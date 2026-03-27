# Web Interface

The web interface is a standalone single-page application (SPA) that communicates with the Yodoca backend through the **web_channel** extension API. The frontend and the backend are independent processes: the backend runs as part of the Yodoca runtime (Python), and the frontend is a separate Node.js application (Vue 3 + Vite).

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Browser                                                     │
│  Vue 3 SPA  (http://localhost:5173)                          │
│  ┌────────────────────┐  ┌─────────────────────────────┐     │
│  │ REST (/api/*)      │  │ AG-UI SSE (POST /agent)     │     │
│  │ threads, projects, │  │ streaming chat via          │     │
│  │ notifications      │  │ @ag-ui/client + @ag-ui/core │     │
│  └────────┬───────────┘  └──────────┬──────────────────┘     │
└───────────┼─────────────────────────┼────────────────────────┘
            │ Vite dev proxy          │ Vite dev proxy
            │ /api → :8080            │ /agent → :8080
            ▼                         ▼
┌──────────────────────────────────────────────────────────────┐
│  web_channel (FastAPI + uvicorn)  http://127.0.0.1:8080      │
│                                                              │
│  routes_api.py     routes_agui.py     routes_openai.py       │
│  /api/*            POST /agent        /v1/*                  │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │ RequestBridge                                       │     │
│  │ busy guard · Future/Queue · notifications ring buf  │     │
│  └───────────────────────┬─────────────────────────────┘     │
│                          │ ctx.emit("user.message", ...)     │
└──────────────────────────┼───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Yodoca Kernel                                               │
│  EventBus → MessageRouter → Orchestrator → Agent             │
│  Response → channel.on_stream_* / send_to_user               │
└──────────────────────────────────────────────────────────────┘
```

The frontend communicates with the backend via two transport mechanisms:

- **REST** (`/api/*`) — thread management, project management, health checks, long-poll notifications.
- **AG-UI over SSE** (`POST /agent`) — real-time chat streaming using the [AG-UI protocol](https://docs.ag-ui.com). The frontend sends a message and receives a stream of typed events (run started, text deltas, run finished).

In development, Vite proxies `/api` and `/agent` to `http://127.0.0.1:8080` so the frontend and backend run on different ports without CORS issues.

---

## Getting Started

### Prerequisites

- Python 3.12+ with `uv` (backend)
- Node.js 18+ with `npm` (frontend)

### Running

The backend and frontend are started separately:

```bash
# 1. Start the Yodoca backend (includes web_channel on port 8080)
uv run python -m supervisor

# 2. In a separate terminal, start the frontend dev server
cd web
npm install
npm run dev
```

The frontend opens at `http://localhost:5173`. The Vite dev server proxies API calls to the backend automatically.

### Production Build

```bash
cd web
npm run build
```

The build output goes to `web/dist/`. It can be served by any static file server or embedded into the backend's static file serving (not configured by default).

---

## Frontend Application

### Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | Vue 3.5 (Composition API, `<script setup>`) |
| Language | TypeScript (strict mode) |
| Build | Vite 7 |
| Routing | Vue Router 4 |
| State | Pinia 3 |
| Styling | Tailwind CSS 4 |
| UI components | reka-ui (Radix-like primitives), shadcn-vue (New York style) |
| Icons | lucide-vue-next |
| Chat streaming | @ag-ui/client, @ag-ui/core (AG-UI protocol) |
| Markdown | markdown-it (linkify, typographer, breaks) |
| Code highlighting | highlight.js (Python, JSON, plaintext; `github-dark` theme) |
| Sanitization | DOMPurify |
| Utilities | @vueuse/core, clsx, class-variance-authority, tailwind-merge |
| Testing | Vitest 4, happy-dom, @vue/test-utils |
| Linting | ESLint + eslint-plugin-vue + typescript-eslint |
| Formatting | Prettier |

### Project Structure

The frontend follows a feature-sliced design inspired by FSD methodology:

```
web/
├── index.html                     # HTML entry point
├── package.json                   # Dependencies and scripts
├── vite.config.ts                 # Vite configuration (proxy, aliases)
├── tsconfig.json                  # TypeScript config
├── components.json                # shadcn-vue config (New York style)
└── src/
    ├── main.ts                    # App bootstrap (Vue + Pinia + Router)
    ├── App.vue                    # Root component (<RouterView />)
    ├── lib/utils.ts               # cn() helper (clsx + tailwind-merge)
    │
    ├── app/
    │   ├── router.ts              # Route definitions
    │   └── styles/globals.css     # CSS variables, design tokens, theme
    │
    ├── shared/
    │   ├── api/                   # Backend communication layer
    │   │   ├── http.ts            # apiFetch<T>() — typed REST client
    │   │   ├── auth.ts            # Token resolution (storage / env)
    │   │   ├── agent.ts           # AG-UI streaming (HttpAgent)
    │   │   └── threads.ts         # Thread CRUD REST functions
    │   └── lib/
    │       ├── markdown.ts        # markdown-it + highlight.js + DOMPurify
    │       └── date.ts            # Date formatting utilities
    │
    ├── entities/
    │   ├── message/               # Message model, store, MessageBubble
    │   ├── thread/                # Thread model, store, ThreadItem
    │   └── agent/                 # Agent phase tracking, runAgent()
    │
    ├── features/
    │   └── send-message/          # SendMessageForm component
    │
    ├── widgets/
    │   ├── chat-panel/            # ChatPanel — message list + input
    │   ├── sidebar/               # ThreadSidebar — thread list
    │   └── navigation/            # AppNavigationSidebar
    │
    ├── pages/
    │   ├── chat/ChatPage.vue      # Main chat view
    │   ├── inbox/InboxPage.vue    # Inbox (placeholder)
    │   ├── projects/ProjectsPage.vue
    │   ├── schedule/SchedulePage.vue
    │   └── agents/AgentsPage.vue
    │
    └── components/ui/             # Base UI primitives (shadcn-vue)
        ├── button/
        ├── textarea/
        ├── separator/
        ├── scroll-area/
        └── tooltip/
```

### Pages and Routing

| Route | Component | Description |
|-------|-----------|-------------|
| `/` | — | Redirects to `/chat` |
| `/chat` | ChatPage | Main chat interface (new thread) |
| `/chat/:threadId` | ChatPage | Chat with a specific thread loaded |
| `/inbox` | InboxPage | Notifications and inbox (placeholder) |
| `/projects` | ProjectsPage | Project management (placeholder) |
| `/schedule` | SchedulePage | Scheduled tasks (placeholder) |
| `/agents` | AgentsPage | Agent configuration (placeholder) |

### State Management (Pinia)

The application uses three Pinia stores:

**`useThreadStore`** — manages the threads list and active thread.
- Loads threads from REST API, creates/renames/archives threads.
- Syncs `activeThreadId` with the router (URL ↔ state).
- Provides `sortedThreads` (by `updated_at` descending).

**`useMessageStore`** — manages messages grouped by thread.
- `addMessage(threadId, message)` — adds a complete message.
- `appendMessageDelta(threadId, delta)` — appends streaming text to the last assistant message.
- `setThreadMessages(threadId, messages)` — loads history from the backend.
- Parses backend message format (content extraction, timestamps).

**`useAgentStore`** — tracks agent invocation state.
- `phase`: `idle` → `thinking` → `complete` | `error`.
- `runAgent(threadId, text)` — wraps the AG-UI streaming call, updates phase, dispatches deltas to the message store.

### API Client

Communication with the backend happens through two layers:

**REST** (`shared/api/http.ts`, `shared/api/threads.ts`):

- `apiFetch<T>(path, options)` — typed fetch wrapper with Bearer auth and JSON parsing.
- Thread operations: `fetchThreads()`, `fetchThread(id)`, `createThread(title)`, `updateThread(id, data)`, `deleteThread(id)`.

**AG-UI streaming** (`shared/api/agent.ts`):

- Uses `HttpAgent` from `@ag-ui/client` to call `POST /agent`.
- Sends an `AgUIRunRequest` with `threadId`, `runId`, `messages`, and optional auth headers.
- Receives Server-Sent Events stream with typed AG-UI events.
- `TEXT_MESSAGE_CONTENT` deltas are forwarded to `onDelta` callback → message store → reactive UI update.
- Resolves with the accumulated assistant text on `RUN_FINISHED`.

**Authentication** (`shared/api/auth.ts`):

- Token resolution priority: `sessionStorage` / `localStorage` keys (`yodoca.api_token`, `yodoca.api_key`) → `VITE_API_KEY` environment variable.
- When no token is configured, requests are sent without `Authorization` (works when the backend has auth disabled).

### UI and Design

The interface uses a dark theme with glass-panel aesthetics. Design tokens are defined as HSL CSS variables in `globals.css` (background, foreground, primary, muted, accent, destructive, etc.).

Key widgets:

- **ChatPanel** — the main chat area with a header (thread title, actions), scrollable message list, and input footer with agent status display.
- **MessageBubble** — user messages appear as plain text on the right; assistant messages are rendered as Markdown via `v-html` with `renderMarkdown()` (markdown-it + highlight.js + DOMPurify).
- **SendMessageForm** — auto-resizing textarea, submit on Enter, context chips (UI placeholders), attach and voice buttons.
- **ThreadSidebar** — scrollable list of threads with search, new-thread button, and active highlight.

---

## Backend API (web_channel)

The web_channel extension (`sandbox/extensions/web_channel/`) exposes an HTTP API on `http://127.0.0.1:8080` (configurable). It implements three protocols: `ChannelProvider`, `StreamingChannelProvider`, and `ServiceProvider`.

### Endpoint Groups

The API is organized into three groups:

#### AG-UI Protocol (`/agent`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/agent` | Run the agent; returns AG-UI events over SSE |

This is the primary chat endpoint used by the web frontend. The request body follows the AG-UI `RunAgentInput` schema:

```json
{
  "threadId": "uuid",
  "runId": "uuid",
  "messages": [{"role": "user", "content": "Hello"}],
  "tools": [],
  "context": [],
  "forwardedProps": {}
}
```

The response is an SSE stream with AG-UI event types:
- `RUN_STARTED` — agent invocation begins
- `TEXT_MESSAGE_START` — assistant message begins
- `TEXT_MESSAGE_CONTENT` — text delta (streaming token)
- `TEXT_MESSAGE_END` — assistant message complete
- `STEP_STARTED` / `STEP_FINISHED` — tool use status
- `RUN_FINISHED` — agent invocation complete
- `RUN_ERROR` — error occurred

#### OpenAI-Compatible API (`/v1/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/models` | List available models |
| POST | `/v1/chat/completions` | Chat Completions (JSON or SSE) |
| POST | `/v1/responses` | Responses API (JSON or SSE) |

These endpoints follow the OpenAI API format, enabling compatibility with third-party clients (LibreChat, LobeChat, Open WebUI, ChatBox, etc.). The system exposes a virtual model named `yodoca` (configurable). Both Chat Completions and Responses endpoints support `stream: true` for SSE streaming.

The `X-Thread-Id` header binds requests to named threads for multi-session support.

#### Custom REST API (`/api/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check with uptime |
| GET | `/api/threads` | List threads |
| POST | `/api/threads` | Create a new thread |
| GET | `/api/threads/{id}` | Get thread with message history |
| PATCH | `/api/threads/{id}` | Update thread (rename, etc.) |
| DELETE | `/api/threads/{id}` | Archive thread |
| GET | `/api/projects` | List projects including `description`, `icon`, `files`, and `links` |
| POST | `/api/projects` | Create project with metadata, file paths, and project links |
| GET | `/api/projects/{id}` | Get project |
| PATCH | `/api/projects/{id}` | Update project |
| DELETE | `/api/projects/{id}` | Delete project |
| GET | `/api/notifications` | Long-poll for proactive notifications |

Thread and project endpoints use the core persistence services via `ExtensionContext`. Notifications use an in-memory ring buffer with long-poll delivery (default timeout: 30s).

### Authentication

Bearer token authentication via `Authorization: Bearer <key>`:

- The key is configured in `config.api_key` (manifest or `settings.yaml`) or read from the secret `web_channel.api_key`.
- When no key is configured, authentication is disabled (local development convenience; a warning is logged at startup).
- Invalid or missing token returns HTTP 401.
- The auth middleware applies to all routes.

### Streaming

All streaming uses **Server-Sent Events** (SSE). There are no WebSocket connections.

The `RequestBridge` mediates between HTTP request/response semantics and the kernel's callback-based channel protocol:

1. The HTTP handler calls `bridge.acquire_wait(request_timeout)` (busy guard: waits in queue up to the configured timeout; `acquire()` remains for fail-fast callers).
2. A stream queue is created via `bridge.create_stream_queue()`.
3. The handler emits `user.message` to the kernel.
4. The kernel invokes the agent, which calls `StreamingChannelProvider` callbacks.
5. Each callback pushes an event to the bridge queue.
6. The HTTP handler consumes the queue and yields SSE chunks.
7. On stream end, the busy guard is released.

**Busy guard:** Only one request can be active at a time. Concurrent requests receive `503 Service Unavailable` with `Retry-After: 5`.

SSE format depends on the endpoint:
- **AG-UI** (`/agent`) — uses `EventEncoder` from the `ag_ui` package.
- **Chat Completions** (`/v1/chat/completions`) — OpenAI `chat.completion.chunk` format, terminated by `data: [DONE]`.
- **Responses** (`/v1/responses`) — OpenAI Responses API event types (`response.created`, `response.output_text.delta`, `response.completed`, etc.).

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `host` | `127.0.0.1` | HTTP bind address |
| `port` | `8080` | HTTP port |
| `api_key` | `""` | Bearer token (empty = auth disabled) |
| `cors_origins` | `["*"]` | Allowed CORS origins |
| `request_timeout_seconds` | `120` | Max time waiting for agent response |
| `model_name` | `yodoca` | Virtual model ID for `/v1/models` |
| `default_user_id` | `web_user` | Stable user ID for `user.message` events |

Override in `config/settings.yaml`:

```yaml
extensions:
  web_channel:
    host: "0.0.0.0"
    port: 8080
    api_key: "sk-my-secret-key"
    cors_origins: ["http://localhost:5173"]
```

### Extension Structure

```
sandbox/extensions/web_channel/
├── manifest.yaml          # Extension manifest and config defaults
├── main.py                # WebChannelExtension: lifecycle, protocol impl
├── app.py                 # FastAPI app factory, CORS, auth middleware
├── bridge.py              # RequestBridge: busy guard, Future/Queue, notifications
├── models.py              # Pydantic request/response models
├── streaming.py           # SSE formatters (Chat Completions, Responses API)
├── routes_openai.py       # /v1/models, /v1/chat/completions, /v1/responses
├── routes_api.py          # /api/* (health, threads, projects, notifications)
└── routes_agui.py         # POST /agent (AG-UI protocol)
```

---

## OpenAPI Specification

The full API specification is available at [api/openapi.yaml](api/openapi.yaml) (OpenAPI 3.1, version 0.5.0). It covers all endpoint groups, request/response schemas, authentication, and error responses.

When the backend is running, FastAPI also serves auto-generated interactive documentation at `http://127.0.0.1:8080/docs` (Swagger UI) and `http://127.0.0.1:8080/redoc` (ReDoc).

---

## Development

### Frontend

```bash
cd web

npm install            # Install dependencies
npm run dev            # Start Vite dev server (port 5173)
npm run build          # Production build (vue-tsc + vite build)
npm run preview        # Preview production build

npm run test           # Run tests in watch mode (Vitest)
npm run test:run       # Run tests once

npm run lint           # ESLint check
npm run lint:fix       # ESLint auto-fix
npm run format         # Prettier format
npm run format:check   # Prettier check
```

Environment variables (optional, in `web/.env.local`):

| Variable | Description |
|----------|-------------|
| `VITE_API_KEY` | API key for backend auth (dev convenience) |

### Backend

The web_channel starts automatically with the Yodoca runtime. No separate backend setup is needed beyond the standard `uv run python -m supervisor`.

To test the API directly:

```bash
# Health check
curl http://127.0.0.1:8080/api/health

# List threads
curl http://127.0.0.1:8080/api/threads

# Chat via AG-UI (SSE stream)
curl -N -X POST http://127.0.0.1:8080/agent \
  -H "Content-Type: application/json" \
  -d '{"threadId":"test","runId":"r1","messages":[{"role":"user","content":"Hello"}]}'

# Chat via OpenAI Chat Completions
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"yodoca","messages":[{"role":"user","content":"Hello"}],"stream":false}'
```

Add `-H "Authorization: Bearer <key>"` if authentication is enabled.

---

## References

- [channels.md](channels.md) — Channel system overview (CLI, Telegram, Web)
- [api/openapi.yaml](api/openapi.yaml) — OpenAPI specification
- [ADR 026](adr/026-web-channel.md) — Web Channel architectural decision
- [ADR 027](adr/027-session-project-domain-model.md) — Thread and Project domain model
- [extensions.md](extensions.md) — Extension architecture
- [architecture.md](architecture.md) — System architecture
