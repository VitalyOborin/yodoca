# Channels

Channels are **ChannelProvider** extensions that receive user input and deliver agent responses. They bridge external interfaces (CLI, Telegram, etc.) to the Event Bus and MessageRouter.

---

## Overview

| Channel | Extension ID | Interface | Use case |
|---------|--------------|-----------|----------|
| **CLI** | `cli_channel` | stdin/stdout REPL | Local development, debugging |
| **Telegram** | `telegram_channel` | Telegram Bot API (aiogram) | Remote access, mobile |

Channels are mutually compatible: multiple can be enabled. Each emits `user.message` with its `channel_id`; responses are routed to the channel that originated the message (or the default channel for proactive notifications).

---

## ChannelProvider Protocol

```python
@runtime_checkable
class ChannelProvider(Protocol):
    async def send_to_user(self, user_id: str, message: str) -> None:
        """Deliver message to user through this channel."""
```

Defined in `core/extensions/contract.py`. Loader detects via `isinstance` and registers channels in MessageRouter. When the kernel handles `user.message`, it resolves `channel_id` → ChannelProvider, invokes the agent, and calls `channel.send_to_user(user_id, response)` on the originating channel.

---

## CLI Channel

**Location:** `sandbox/extensions/cli_channel/`

**Roles:** ChannelProvider (input loop runs as `asyncio.Task` in `start()`, not via ServiceProvider)

**Behaviour:**

- Reads lines from stdin (`input("> ")` via `asyncio.to_thread`) in a loop task created in `start()`
- Emits `user.message` with `user_id="cli_user"`, `channel_id=extension_id`
- `send_to_user` prints to stdout
- Handles `EOFError`/`KeyboardInterrupt` for clean shutdown

**Configuration:** None. No secrets. Enable in manifest.

**Use case:** Local development, quick testing without external services.

---

## Telegram Channel

**Location:** `sandbox/extensions/telegram_channel/`

**Roles:** ChannelProvider + ServiceProvider (aiogram long-polling)

**Behaviour:**

- Uses aiogram 3.x for Telegram Bot API with `run_background()` (ServiceProvider)
- Long-polling receives messages (`allowed_updates=["message"]`); emits `user.message` with `user_id=chat_id`, `channel_id=extension_id`
- Filters by `chat_id` — only messages from the configured chat are processed (both inbound and outbound)
- Token validated via `aiogram.utils.token.validate_token` on initialize
- Default parse mode: HTML (`DefaultBotProperties(parse_mode=ParseMode.HTML)`)
- `send_to_user` sends via `bot.send_message(chat_id, text)`; also verifies `user_id == chat_id`

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

**Setup:**

1. Create a bot via @BotFather; obtain token.
2. Start a chat with the bot; get your chat_id (e.g. via @userinfobot or bot logs).
3. Set KV keys: `kv_set telegram_channel.token <token>`, `kv_set telegram_channel.chat_id <chat_id>`
4. Restart the agent.

---

## Message Flow

```
User types in CLI or sends Telegram message
  → Channel receives input
  → ctx.emit("user.message", {text, user_id, channel_id})
  → EventBus → kernel handler
  → router.handle_user_message()
  → Orchestrator runs
  → router calls channel.send_to_user(user_id, response)
  → User sees response in CLI or Telegram
```

---

## Adding a New Channel

1. Create `sandbox/extensions/my_channel/` with `manifest.yaml` and `main.py`
2. Implement `ChannelProvider.send_to_user`
3. In `start()` or `run_background()`, receive user input and emit:

   ```python
   await self._ctx.emit("user.message", {
       "text": user_input,
       "user_id": user_id,
       "channel_id": self._ctx.extension_id,
   })
   ```

4. Register as ChannelProvider (Loader detects via `isinstance`)

---

## References

- [extensions.md](extensions.md) — Extension architecture
- [event_bus.md](event_bus.md) — Event Bus and `user.message` topic
