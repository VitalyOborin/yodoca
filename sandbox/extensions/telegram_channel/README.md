# Telegram Channel Extension

Telegram Bot API integration via **aiogram** long-polling. Implements Extension, ChannelProvider, and ServiceProvider contracts.

## Features

- **Polling only** — no webhooks; works behind NAT/firewalls
- **aiogram 3.x** — async framework for Telegram Bot API
- **Extension contracts** — Extension, ChannelProvider, ServiceProvider
- **Event Bus** — emits `user.message` for incoming text; receives responses via `send_to_user`

## Setup

1. Create a Telegram bot via [BotFather](https://t.me/BotFather) and get the bot token.
2. Get your chat_id (e.g. send a message to your bot, then call `getUpdates` on the API, or use @userinfobot).
3. Store both in the KV store:
   - `telegram_channel.token` — bot token for Telegram Bot API
   - `telegram_channel.chat_id` — chat_id for communicating with the user

Example: use the `kv_set` tool to set both keys and restart.

### Optional configuration

- **polling_timeout** — long-polling timeout in seconds (default: 30).

## Notes

- Only messages from the configured `telegram_channel.chat_id` are processed.
- Responses are sent only to that chat_id.
- The extension runs as a background service; polling starts automatically on app startup.
