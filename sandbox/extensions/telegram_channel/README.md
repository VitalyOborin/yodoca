# Telegram Channel Extension

Telegram Bot API integration via **aiogram** long-polling.

## Implemented protocols

- **Extension** — lifecycle (initialize, start, stop, destroy, health_check)
- **ChannelProvider** — `send_to_user`, `send_message` (reactive and proactive delivery)
- **StreamingChannelProvider** — token-by-token streaming via debounced message edits and typing indicator
- **ServiceProvider** — `run_background` runs aiogram long-polling loop with exponential backoff
- **SetupProvider** — interactive configuration (token via keyring, chat_id auto-captured)
- **ContextProvider** — injects Telegram channel readiness status into the agent system prompt

## How it works

- **Polling only** — no webhooks; works behind NAT/firewalls
- **aiogram 3.x** — async framework for Telegram Bot API
- **Single-user** — always delivers to the one configured `chat_id`; multi-user is not supported
- **Event Bus** — emits `user.message` for incoming text; receives responses via `send_to_user`
- **Streaming** — intermediate edits with debouncing; typing indicator heartbeat every 4 s

## Setup

Use the interactive setup flow (no manual KV access needed):

1. Ask the agent to configure the Telegram channel, or call `configure_extension(extension_id="telegram_channel")`.
2. The agent collects the Bot API token via `request_secure_input` — the token is stored in keyring, never exposed to the LLM.
3. The bot auto-captures `chat_id` from the first message the user sends in Telegram (`/start`).

The extension activates immediately after the token is saved — no restart required.

## Configuration (`config/settings.yaml`)

```yaml
extensions:
  telegram_channel:
    token_secret: telegram_bot_token          # keyring secret name (default)
    polling_timeout: 10                        # long-polling timeout in seconds (default: 10)
    streaming_enabled: true
    stream_edit_interval_ms: 500               # min ms between stream edits
    stream_min_chunk_chars: 20                 # min chars before first edit
```

## Notes

- Only messages from the auto-captured (or pre-configured) `chat_id` are processed.
- Responses are always sent to that `chat_id`.
- The extension runs as a background service; polling starts automatically on app startup.
- If the token or `chat_id` is missing, `send_to_user` raises a `RuntimeError` so the agent gets an explicit error instead of a silent no-op.
