# ADR 002: Nano-Kernel + Extensions

## Status

Accepted.

## Context

In assistant3, the extension system was built around an Event Bus, 6 extension types, a permission/security layer, 7 lifecycle states, and a ~800-line `ExtensionManager`. This architecture proved over-engineered for a single-user AI agent that runs locally: Redis Streams added external dependencies, the approval workflow duplicated what the agent could handle in conversation, and the permission system enforced constraints that a sandbox directory convention covers just as well.

For assistant4 we need a radically simpler design that preserves the core value — **all functionality lives in extensions** — while eliminating infrastructure that didn't earn its complexity.

## Decision

### 1. Key Idea

The application consists of a **nano-kernel** and **extensions**. The kernel only knows how to start the agent and load extensions. All functionality lives in extensions.

```
┌─────────────────────────────────────────┐
│              SUPERVISOR                  │  process watcher, ~100 lines
│         spawn · monitor · restart        │
└────────────────────┬────────────────────┘
                     │ subprocess
┌────────────────────▼────────────────────┐
│              NANO-KERNEL                 │
│                                          │
│   Loader ──► Agent ──► MessageRouter    │  ~400 lines total
│                                          │
└──────┬───────────────┬──────────────────┘
       │ initialize()  │ initialize()
       ▼               ▼
 ┌──────────┐    ┌──────────┐    ┌──────────┐
 │  cli_    │    │ telegram │    │  memory  │  sandbox/extensions/
 │ channel  │    │ _channel │    │          │  each in its own folder
 └──────────┘    └──────────┘    └──────────┘
```

Supervisor is not infrastructure. It holds no Bus, no state. It simply watches the kernel process.

### 2. Architectural Principles

| Principle | What it means in practice |
|---|---|
| **Direct calls instead of Bus** | A channel extension receives an `on_user_message` callback and calls it directly. No routing, no serialization, no external dependencies |
| **Extension owns its loop** | Telegram runs its own polling loop. CLI reads stdin on its own. The kernel has no knowledge of their internals |
| **Push, not Pull** | An extension wakes the agent when a message arrives. The agent does not poll channels |
| **Context is the only entry point** | Extensions do not import anything from `core/`. Everything goes through the injected `ExtensionContext` |
| **Manifest is the contract for Builder Agent** | `manifest.yaml` is read not only by the runtime but also by the LLM when generating a new extension. The simpler it is, the better the generation |

### 3. Extension Types

Four types. Not six, not seven — four. Each type answers the question: **"what does the extension do in relation to the agent?"**

```
┌─────────────┬────────────────────────────────────────────────────────────┐
│    Type      │  What it does                                             │
├─────────────┼────────────────────────────────────────────────────────────┤
│ tool        │  Adds tools to the agent. The agent calls them explicitly. │
│             │  Examples: calculator, search, kv-store, memory search     │
├─────────────┼────────────────────────────────────────────────────────────┤
│ channel     │  User communication channel. Receives messages and sends  │
│             │  responses. Manages its own I/O loop.                      │
│             │  Examples: CLI, Telegram, Web UI, Slack                    │
├─────────────┼────────────────────────────────────────────────────────────┤
│ service     │  Background service. Lives independently, provides         │
│             │  capabilities to other extensions via depends_on.          │
│             │  Examples: memory store, sqlite wrapper, http cache        │
├─────────────┼────────────────────────────────────────────────────────────┤
│ scheduler   │  Periodic task by cron. Can return an alert — then the    │
│             │  kernel sends a notification to the user.                  │
│             │  Examples: reminders, price monitoring, daily report       │
└─────────────┴────────────────────────────────────────────────────────────┘
```

An extension can implement **multiple types simultaneously**. A memory extension is a `service` (runs in the background) + `tool` (provides tools to the agent). This is normal and expected.

Middleware and Monitor are not separate types. Middleware is just `context.subscribe()` in `initialize()`. Monitor is a Scheduler whose `execute()` returns `dict | None`.

### 4. Extension Contract

#### Base Protocol — required for all extensions

```python
class Extension(Protocol):
    id: str       # matches folder name: "telegram_channel"
    name: str
    version: str

    async def initialize(self, context: ExtensionContext) -> None:
        """Called once on load. Subscriptions, dependency init."""

    async def start(self) -> None:
        """Start active work: polling loops, servers, background tasks."""

    async def stop(self) -> None:
        """Graceful shutdown. Cancel tasks, close connections."""

    async def destroy(self) -> None:
        """Release resources. Called after stop()."""

    def health_check(self) -> bool:
        """True = operating normally."""
```

#### Specialized Protocols

```python
class ToolProvider(Protocol):
    def get_tools(self) -> list[Any]:
        """List of @function_tool objects for the agent."""

class ChannelProvider(Protocol):
    async def send_to_user(self, user_id: str, message: str) -> None:
        """Send agent response to user through this channel."""

class ServiceProvider(Protocol):
    async def run_background(self) -> None:
        """Main service loop. Must handle CancelledError."""

class SchedulerProvider(Protocol):
    def get_schedule(self) -> str:
        """Cron expression: '*/5 * * * *'"""

    async def execute(self) -> dict[str, Any] | None:
        """Run the task. Return {'text': '...'} to notify user."""
```

#### SetupProvider — for extensions that need configuration

```python
class SetupProvider(Protocol):
    def get_setup_schema(self) -> list[dict]:
        """[{name, description, secret, required}] — list of setup parameters."""

    async def apply_config(self, name: str, value: str) -> None:
        """Save config value. Extension decides where to store it."""

    async def on_setup_complete(self) -> tuple[bool, str]:
        """Verify everything is set up. Return (success, message)."""
```

### 5. ExtensionContext — Kernel API for Extensions

Everything an extension can do — only through this object. No direct imports from `core/`.

```python
class ExtensionContext:
    extension_id: str          # "telegram_channel" — for logs, data_dir
    config: dict               # values from manifest.yaml → config:
    logger: Logger             # logging.getLogger(f"ext.{extension_id}")

    # ── User interaction ─────────────────────────────────────────────────
    on_user_message: Callable  # channel calls this when a message arrives
                               # async (text, user_id, channel: ChannelProvider) -> None

    async def notify_user(self, user_id: str, text: str,
                          channel_id: str | None = None) -> None:
        """Send notification to user (from scheduler, monitor, service)."""

    # ── Agent ─────────────────────────────────────────────────────────────
    async def invoke_agent(self, prompt: str) -> str:
        """Ask the agent to process a prompt and return a response."""

    # ── Secrets and config ────────────────────────────────────────────────
    async def get_secret(self, name: str) -> str | None:
        """Get a secret by name from .env."""

    def get_config(self, key: str, default=None) -> Any:
        """Read a value from the config: block in manifest.yaml."""

    # ── Dependencies ──────────────────────────────────────────────────────
    def get_extension(self, extension_id: str) -> Any:
        """Get an instance of another extension (only from depends_on)."""

    # ── Filesystem ────────────────────────────────────────────────────────
    @property
    def data_dir(self) -> Path:
        """Private extension folder: sandbox/data/<extension_id>/
        Created automatically. For SQLite, caches, any data."""

    # ── Process control ───────────────────────────────────────────────────
    def request_restart(self) -> None:
        """Ask supervisor to restart the kernel (after installing a new extension)."""

    def request_shutdown(self) -> None:
        """Shut down the application."""
```

**What is NOT in Context:** Event Bus, priorities, checkpoint/resume, SandboxFS with ACL, emit/subscribe as a general mechanism. Only what an extension actually needs to work.

### 6. Manifest

```yaml
# sandbox/extensions/telegram_channel/manifest.yaml

id: telegram_channel
name: Telegram Bot Channel
version: "1.0.0"
type: channel              # tool | channel | service | scheduler

description: >
  User communication channel via Telegram bot.
  Receives incoming messages and sends agent responses.

entrypoint: main:TelegramChannelExtension   # module:Class

# Description for agent system prompt
natural_language_description: |
  Telegram channel. User writes to bot in Telegram,
  messages go to the agent. Responses are sent back to Telegram.
  Supports proactive notifications.

# Setup instructions — visible to agent while extension is not configured
setup_instructions: |
  A bot token from @BotFather is needed for setup.
  Call configure_extension("telegram_channel", "token", "<TOKEN>").

# Dependencies: loaded before this extension
depends_on:
  - kv

# Secrets from .env (optional, if not via SetupProvider)
secrets: []

# Extension config (accessible via context.get_config())
config:
  parse_mode: MarkdownV2

enabled: true
```

What is **NOT** in the manifest: `permissions`, `capabilities`, `hooks`, `config_schema`, `packages`. The manifest is the extension's passport, not a security specification.

### 7. Extension ↔ Kernel Interaction

#### Channel: Telegram receives a message

```
Telegram Bot API
    ↓  long-polling (asyncio task inside extension)
    ↓  message arrives
    ↓
telegram_channel._polling_loop()
    ↓
await context.on_user_message(
    text="Hello",
    user_id="123456",
    channel=self          # ← passes itself so kernel knows where to reply
)
    ↓
MessageRouter.handle_user_message()
    ↓
agent.invoke("Hello")  →  LLM  →  "Hi! How can I help?"
    ↓
channel.send_to_user("123456", "Hi! How can I help?")
    ↓
Telegram Bot API (send)
```

#### Scheduler: reminder in 2 hours

```
scheduler extension._tick_loop()
    ↓  time is up
    ↓
result = await execute()  →  {"text": "Reminder: call mom"}
    ↓
await context.notify_user(user_id, result["text"])
    ↓
MessageRouter.notify_user()
    ↓  finds active channel
channel.send_to_user(user_id, "Reminder: call mom")
```

#### Tool: agent calls a tool from memory extension

```
agent.invoke("what did we discuss yesterday?")
    ↓
LLM decides to call tool memory_search
    ↓
memory_extension.search("yesterday")  →  [...]
    ↓
LLM formulates response based on result
```

### 8. Extension Lifecycle

Three states — not seven:

```
INACTIVE ──► ACTIVE ──► ERROR
               │
            (restart)
               │
            INACTIVE
```

| State | When |
|---|---|
| `INACTIVE` | Loaded, initialized, not yet started |
| `ACTIVE` | `start()` called, running normally |
| `ERROR` | `start()` failed or `health_check()` returned False |

Loader on startup runs: `discover → load → initialize → start`. On new extension install (Builder Agent): `generate code → write files → request_restart()` → supervisor restarts kernel → standard startup.

### 9. Project Structure

```
supervisor/              ← process watcher (~100 lines, as-is)
  __main__.py
  runner.py

core/                    ← nano-kernel (~400 lines total)
  __main__.py            ← python -m core
  settings.py
  agent.py               ← create_agent(loader) → Agent
  loader.py              ← discover + import + initialize + start
  router.py              ← handle_user_message + notify_user
  context.py             ← ExtensionContext
  contract.py            ← Extension + 4 protocols + SetupProvider
  manifest.py            ← ExtensionManifest (Pydantic)

sandbox/
  extensions/            ← everything Builder Agent writes
    cli_channel/
      manifest.yaml
      main.py
    telegram_channel/
      manifest.yaml
      main.py
    kv/
    memory/
    task_scheduler/      ← scheduler extension, manages deferred tasks
    ...
  data/                  ← private data for each extension
    telegram_channel/
    memory/
    task_scheduler/

prompts/
  orchestrator.jinja2
  builder.jinja2         ← Builder Agent: contract + code examples

config/
  settings.yaml

.env                     ← secrets
```

### 10. Builder Agent — How It Uses This Concept

Builder Agent sees three things when generating a new extension:

1. **`core/contract.py`** — full text with protocols (few-shot reference)
2. **Two working examples** — full code of `telegram_channel/main.py` and `kv/main.py` directly in the prompt
3. **Task** — description of what to create from Orchestrator

It generates exactly two files: `manifest.yaml` and `main.py`. After writing the files, it calls `context.request_restart()`. Supervisor notices a flag file and restarts the kernel. On the next startup, Loader discovers the new extension and loads it.

## Consequences

### Comparison with the Previous Architecture

| v1 (assistant3) | v2 (assistant4) |
|---|---|
| Event Bus — central nervous system | Direct callbacks — no routing |
| 6 extension types + Middleware | 4 types; middleware is just `initialize()` |
| Redis Streams | In-memory asyncio → no external dependencies |
| Permission system (network.http, filesystem.write, ...) | No ACL; sandbox is a convention |
| 7 lifecycle states | 3 states |
| ~800-line manager.py | ~200-line loader.py |
| Approval workflow in the kernel | Agent asks in chat — its responsibility |
| Security policy YAML | None; trust extensions in sandbox |

### Trade-offs

- **Gained:** drastically lower complexity (~400 lines of kernel vs ~2000+), no external dependencies, faster startup, easier extension development, better LLM-generated code quality (simpler contract = fewer mistakes).
- **Lost:** formal security enforcement (permissions, ACL), event-driven decoupling between extensions, hot-reload without process restart, structured approval workflow. These are acceptable losses for a single-user locally-running agent.
