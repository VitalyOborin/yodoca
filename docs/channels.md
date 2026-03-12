# Channels

Channels are **ChannelProvider** extensions that receive user input and deliver agent responses. They bridge external interfaces (CLI, Telegram, etc.) to the Event Bus and MessageRouter.

---

## Overview

| Channel | Extension ID | Interface | Use case |
|---------|--------------|-----------|----------|
| **CLI** | `cli_channel` | stdin/stdout REPL | Local development, debugging |
| **Telegram** | `telegram_channel` | Telegram Bot API (aiogram) | Remote access, mobile |
| **Web** | `web_channel` | HTTP + SSE (FastAPI/uvicorn) | Browser frontends, OpenAI-compatible clients, app integrations |

Channels are mutually compatible: multiple can be enabled. Each emits `user.message` with its `channel_id`; responses are routed to the channel that originated the message (or the default channel for proactive notifications).

---

## ChannelProvider Protocol

```python
@runtime_checkable
class ChannelProvider(Protocol):
    async def send_to_user(self, user_id: str, message: str) -> None:
        """Reactive: reply to a specific user who sent a message."""

    async def send_message(self, message: str) -> None:
        """Proactive: deliver to the channel's default recipient.
        All addressing (user_id, chat_id, etc.) is internal to the channel."""
```

Defined in `core/extensions/contract.py`. Loader detects via `isinstance` and registers channels in MessageRouter. When the kernel handles `user.message`, it resolves `channel_id` → ChannelProvider, invokes the agent, and calls `channel.send_to_user(user_id, response)` on the originating channel. For proactive delivery (e.g. scheduled reminders, task notifications), the kernel calls `channel.send_message(text)` — the channel handles addressing internally.

---

## Streaming

Channels can optionally implement **StreamingChannelProvider** (in addition to `ChannelProvider`) to receive incremental response delivery instead of a single complete message. The kernel detects streaming via `isinstance(channel, StreamingChannelProvider)` and branches in `handle_user_message()`: streaming channels get token-by-token chunks and status updates; non-streaming channels get the same behaviour as before (`invoke_agent()` then `send_to_user()`).

**Protocol** (`core/extensions/contract.py`):

| Method | When | Purpose |
|--------|------|---------|
| `on_stream_start(user_id)` | Before agent run | Typing indicator, placeholder message |
| `on_stream_chunk(user_id, chunk)` | Each text delta | Append to buffer or display |
| `on_stream_status(user_id, status)` | Tool call / handoff | e.g. "Using: search_memory" |
| `on_stream_end(user_id, full_text)` | After completion | Final message, cleanup |

**Lifecycle:** `on_stream_start` → zero or more `on_stream_chunk` / `on_stream_status` → `on_stream_end`. The kernel holds the agent lock for the whole stream; `agent_response` is emitted after the stream ends with the full text. See [ADR 010](adr/010-streaming.md).

---

## CLI Channel

**Location:** `sandbox/extensions/cli_channel/`

**Roles:** ChannelProvider (input loop runs as `asyncio.Task` in `start()`, not via ServiceProvider)

**Behaviour:**

- Reads lines from stdin (`input("> ")` via `asyncio.to_thread`) in a loop task created in `start()`
- Emits `user.message` with `user_id="cli_user"`, `channel_id=extension_id`
- `send_to_user` prints to stdout
- Handles `EOFError`/`KeyboardInterrupt` for clean shutdown
- **Streaming:** Implements `StreamingChannelProvider`. When `streaming_enabled` is true, prints chunks as they arrive; `on_stream_status` prints e.g. `[Using: tool_name]`. When disabled, buffers and prints once at `on_stream_end` (useful for debugging).

**Configuration:** Optional `config.streaming_enabled` (default `true`). No secrets. Enable in manifest.

**Use case:** Local development, quick testing without external services.

---

## Telegram Channel

**Location:** `sandbox/extensions/telegram_channel/`

**Roles:** ChannelProvider + ServiceProvider (aiogram long-polling); optionally **StreamingChannelProvider**

**Behaviour:**

- Uses aiogram 3.x for Telegram Bot API with `run_background()` (ServiceProvider)
- Long-polling receives messages (`allowed_updates=["message"]`); emits `user.message` with `user_id=chat_id`, `channel_id=extension_id`
- Filters by `chat_id` — only messages from the configured chat are processed (both inbound and outbound)
- Token validated via `aiogram.utils.token.validate_token` on initialize
- Default parse mode: HTML (`DefaultBotProperties(parse_mode=ParseMode.HTML)`)
- `send_to_user` sends via `bot.send_message(chat_id, text)`; also verifies `user_id == chat_id`
- **Streaming:** Implements `StreamingChannelProvider`. Simulates streaming by sending an initial "..." message, then editing it as chunks arrive (debounced by `stream_edit_interval_ms` and `stream_min_chunk_chars` to respect Telegram rate limits). Shows a **typing indicator** at stream start and repeats `send_chat_action(chat_id, "typing")` every 4 seconds in a background task until the response is complete. Per-user stream state (`message_id`, buffer, typing task) supports multi-user readiness.

**Dependencies:** `kv` extension (required in `depends_on`)

**Configuration (via KV store):**

| Key | Description |
|-----|--------------|
| `telegram_channel.token` | Telegram Bot API token (from @BotFather) |
| `telegram_channel.chat_id` | Chat ID to accept messages from (single-user mode) |

**Manifest config:**

| Key | Default | Description |
|-----|---------|-------------|
| `config.polling_timeout` | 30 | Long-polling timeout in seconds |
| `config.streaming_enabled` | true | Use streaming (edit message + typing) when true |
| `config.stream_edit_interval_ms` | 500 | Min interval between message edits (ms) |
| `config.stream_min_chunk_chars` | 20 | Min characters before an edit |

**Setup:**

1. Create a bot via @BotFather; obtain token.
2. Start a chat with the bot; get your chat_id (e.g. via @userinfobot or bot logs).
3. Set KV keys: `kv_set telegram_channel.token <token>`, `kv_set telegram_channel.chat_id <chat_id>`
4. Restart the agent.

---

## Web Channel

**Location:** `sandbox/extensions/web_channel/`

**Roles:** ChannelProvider + StreamingChannelProvider + ServiceProvider

**Behaviour:**

- Runs FastAPI on uvicorn inside `run_background()`
- Accepts OpenAI-compatible requests on `/v1/models`, `/v1/chat/completions`, `/v1/responses`
- Exposes custom REST endpoints on `/api/health`, `/api/threads`, `/api/projects`, `/api/notifications`
- Uses `RequestBridge` to translate HTTP request/response flow into channel callbacks
- Returns SSE for streaming requests and long-polls proactive notifications on `/api/notifications`
- Enforces a single active request with a busy guard; concurrent requests receive `503` with `Retry-After: 5`
- Supports `X-Thread-Id` so web frontends can bind requests to named runtime threads
- Uses a stable logical user identity (`default_user_id`, default `web_user`) for memory/context continuity

**Configuration:**

| Key | Default | Description |
|-----|---------|-------------|
| `config.host` | `127.0.0.1` | Bind address for uvicorn |
| `config.port` | `8080` | HTTP port |
| `config.api_key` | `""` | Bearer token; when empty, auth is disabled |
| `config.cors_origins` | `["*"]` | Allowed CORS origins |
| `config.request_timeout_seconds` | `120` | Timeout while waiting on the agent |
| `config.model_name` | `yodoca` | Virtual model id returned by `/v1/models` |
| `config.default_user_id` | `web_user` | Stable user id emitted in `user.message` |

**Authentication and transport:**

- Bearer token auth via `Authorization: Bearer <key>`
- If `config.api_key` is empty, the extension also tries secret `web_channel.api_key`
- CORS allows `Authorization`, `Content-Type`, and `X-Thread-Id`
- Streaming uses `text/event-stream`

---

## Agent Channel Tools

The Orchestrator has two tools for agent-driven channel selection (see [ADR 007](adr/007-user-channel-selector.md)):

| Tool | Purpose |
|------|---------|
| `list_channels` | List available channels with IDs and descriptions |
| `send_to_channel(channel_id, text)` | Send a message to the user via a specific channel |

**`list_channels`** returns `ListChannelsResult` (structured):

- `success: bool`
- `channels: list[ChannelInfo]` — each with `channel_id` and `description`
- `error: str | None`

Empty when no channels are registered: `success=True`, `channels=[]`.

**`send_to_channel`** returns `SendToChannelResult` (structured):

- Success: `success=True`, `error=None`
- Error: `success=False`, `error="Channel 'x' not found. Use list_channels to see available channels."`

This enables the agent to reliably detect delivery status and choose channels (e.g. "send to Telegram") based on user preference or escalation context.

---

## Troubleshooting Delivery and Tool Availability

Channels deliver messages, but they do not decide which non-channel tools exist. If the Orchestrator reports that a tool is unavailable (for example shell/web/kv), the root cause is usually extension loading, not channel routing.

Use this quick triage:

1. Confirm the channel path works:
   - incoming `user.message` is emitted by the channel
   - response is delivered through `send_to_user()` / streaming callbacks
2. Check extension load status in `sandbox/logs/app.log`:
   - look for `Failed to load extension <id>`
   - look for import errors in extension `main.py` or sibling modules
3. Restart the agent process after fixing extension code/config so Loader re-runs:
   - `discover` → `load_all` → `initialize_all` → `detect_and_wire_all`
4. Re-test from the same channel.

Important: if a ToolProvider extension fails to load, its tools are excluded from Orchestrator capabilities, while channels can still remain fully functional.

---

## Message Flow

```
User types in CLI, sends Telegram message, or POSTs to web_channel
  → Channel receives input
  → ctx.emit("user.message", {text, user_id, channel_id})
  → EventBus → kernel handler
  → router.handle_user_message()
  → Orchestrator runs
  → router calls channel.send_to_user(user_id, response)
  → User sees response in CLI, Telegram, or the HTTP client
```

---

## Adding a New Channel

1. Create `sandbox/extensions/my_channel/` with `manifest.yaml` and `main.py`
2. Implement `ChannelProvider`: both `send_to_user` (reactive replies) and `send_message` (proactive delivery)
3. Optionally implement **StreamingChannelProvider** (`on_stream_start`, `on_stream_chunk`, `on_stream_status`, `on_stream_end`) for incremental delivery. The kernel uses streaming only when the channel implements this protocol; otherwise it uses `invoke_agent()` and `send_to_user()`.
4. In `start()` or `run_background()`, receive user input and emit:

   ```python
   await self._ctx.emit("user.message", {
       "text": user_input,
       "user_id": user_id,
       "channel_id": self._ctx.extension_id,
   })
   ```

5. Loader detects ChannelProvider (and optionally StreamingChannelProvider) via `isinstance`

---

## References

- [extensions.md](extensions.md) — Extension architecture
- [event_bus.md](event_bus.md) — Event Bus and `user.message` topic
- [ADR 007](adr/007-user-channel-selector.md) — Agent-driven channel selection
- [ADR 010](adr/010-streaming.md) — Streaming response delivery (protocol, router, channels)
- [ADR 026](adr/026-web-channel.md) — Web channel HTTP API

