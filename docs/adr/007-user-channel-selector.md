# ADR 007: Agent-Driven Channel Selection for Outbound Communication

## Status

Proposed

## Context

The system currently supports multiple communication channels (CLI, Telegram) with more planned (Slack, Discord, Web GUI, API). Channels work well for the *reactive* path: a user sends a message via a channel, the agent responds on that same channel. However, the agent cannot **proactively choose** which channel to use for outbound messages.

### Motivating scenarios

| # | User says (CLI) | Expected behavior | Current behavior |
|---|-----------------|--------------------|--------------------|
| 1 | "Передай мне привет в Telegram" | Agent replies "OK" in CLI; "Привет" arrives in Telegram | Agent has no tool to send to a specific channel |
| 2 | "Через 5 минут напомни мне в Telegram выключить чайник" | Agent schedules a reminder; 5 min later Telegram receives the message | Scheduler fires `system.user.notify` but always hits the first registered channel (typically CLI) |
| 3 | Heartbeat escalation with channel preference | Orchestrator delivers the notification on the channel specified in the original task memory | Heartbeat calls `ctx.request_agent_task(reason)` with `channel_id=None` — always default |

### What already works

A careful audit of the codebase shows that most of the plumbing is already in place:

- `MessageRouter.notify_user(text, channel_id=None)` — accepts `channel_id`; when provided, routes to the correct channel.
- `ExtensionContext.notify_user(text, channel_id=None)` — publishes `system.user.notify` with `{text, channel_id}` in payload.
- `ExtensionContext.request_agent_task(prompt, channel_id=None)` — publishes `system.agent.task` with `{prompt, channel_id}` in payload.
- Kernel handler `on_user_notify` reads `channel_id` from the event payload and passes it to `router.notify_user()`.
- Kernel handler `on_agent_task` reads `channel_id` from the event payload and passes it to `router.notify_user()` after agent invocation.
- Scheduler tool docstrings already mention `channel_id` in payload examples (`{"text": "...", "channel_id": null}`).

### Root cause: the missing piece

The **agent** (Orchestrator or any sub-agent) has no way to:

1. **Discover** which channels are available at runtime.
2. **Send** a message to a specific channel via a tool call.

Without these capabilities, the agent cannot reason about channels or act on user requests like "send to Telegram." The plumbing exists end-to-end, but there is no agent-facing surface.

### Secondary issue: `user_id` resolution for proactive messages

`MessageRouter.notify_user()` hardcodes `user_id = "default"` when sending proactive notifications. This works for CLI (`print` ignores `user_id`), but Telegram's `send_to_user` validates `user_id == chat_id` — so a proactive message with `user_id="default"` is silently dropped.

Each channel internally manages its own user identity (CLI: `"cli_user"`, Telegram: `chat_id` from KV store, Slack: workspace user ID, etc.). These are **channel-internal details** — the agent and the router should not know or care about them. But the `ChannelProvider` protocol currently has no method for the router to ask a channel "who should I address this to?" during proactive delivery.

## Decision

### Design principles

1. **Tools, not magic.** The agent selects channels via explicit tool calls, not implicit heuristics.
2. **Channel encapsulation.** The agent knows only channel IDs (e.g. `"telegram_channel"`). All channel internals — tokens, chat IDs, authentication, user identity resolution — remain inside the channel extension. The agent never sees or handles `user_id`, `chat_id`, or any transport-level detail.
3. **No kernel changes.** Channel tools are an extension (or core tools addition) — kernel stays minimal per ADR 002.
4. **Single-user assumption.** Phase 1 targets the single-user deployment. Multi-user routing is out of scope.
5. **Backward compatibility.** `channel_id=None` keeps working as before (first registered channel).

### 1. Extend `ChannelProvider` protocol: `get_default_user_id()`

Add a method to the protocol so that each channel can declare its default recipient for proactive (outbound) messages. This is a **channel-internal contract** — only the router calls it to resolve addressing before `send_to_user()`. The agent never sees this value.

```python
# core/extensions/contract.py

@runtime_checkable
class ChannelProvider(Protocol):
    """User communication channel. Receives messages and sends responses."""

    async def send_to_user(self, user_id: str, message: str) -> None:
        """Send agent response to user through this channel."""

    def get_default_user_id(self) -> str:
        """Return the channel-internal user identifier for proactive messages.
        This is an implementation detail of the channel (e.g. CLI returns 'cli_user',
        Telegram returns chat_id from KV). The agent never sees this value."""
        ...
```

**Why a protocol method, not a sentinel value?** Each channel manages its own user identity internally (string in CLI, numeric `chat_id` in Telegram, workspace user ID in Slack, etc.). The channel is the authority on how to address its user. A sentinel like `"default"` would push this responsibility to each channel's `send_to_user()` implementation, coupling it to a convention instead of an explicit contract.

### 2. Implement `get_default_user_id()` in existing channels

**CLI channel:**

```python
def get_default_user_id(self) -> str:
    return "cli_user"
```

**Telegram channel:**

```python
def get_default_user_id(self) -> str:
    return self._chat_id or ""
```

### 3. Fix `MessageRouter.notify_user()` to resolve `user_id` via the channel

Replace the hardcoded `user_id = "default"`:

```python
# core/extensions/router.py — MessageRouter

async def notify_user(self, text: str, channel_id: str | None = None) -> None:
    """Send notification to user. Uses channel_id if provided, otherwise first channel."""
    if not self._channels:
        logger.warning("notify_user: no channels registered")
        return
    if channel_id and channel_id in self._channels:
        ch = self._channels[channel_id]
    else:
        ch = next(iter(self._channels.values()))
    user_id = ch.get_default_user_id() if hasattr(ch, "get_default_user_id") else "default"
    await ch.send_to_user(user_id, text)
```

The `hasattr` guard ensures backward compatibility with any third-party channel that hasn't implemented the new method yet.

### 4. Add `get_channel_ids()` and `get_channel_descriptions()` to `MessageRouter`

```python
def get_channel_ids(self) -> list[str]:
    """Return list of registered channel extension IDs."""
    return list(self._channels.keys())

def set_channel_descriptions(self, descriptions: dict[str, str]) -> None:
    """Set human-readable channel descriptions (from manifest 'name' field)."""
    self._channel_descriptions = descriptions

def get_channel_descriptions(self) -> dict[str, str]:
    """Return {channel_id: human-readable name} for all registered channels."""
    return getattr(self, "_channel_descriptions", {})
```

`set_channel_descriptions` is called once during bootstrap by the Loader, which already has access to manifest `name` fields. This keeps the router free of manifest awareness while giving tools access to human-readable labels.

### 5. Agent tools: `list_channels` and `send_to_channel`

Two new `@function_tool` tools, created as core tools (not an extension) because they operate on `MessageRouter` directly. The agent interacts only with `channel_id` and `text` — all user identity resolution happens inside the router, invisible to the LLM.

```python
# core/tools/channel.py

from agents import function_tool
from core.extensions.router import MessageRouter


def make_channel_tools(router: MessageRouter) -> list:
    """Create agent tools for channel discovery and targeted messaging."""

    @function_tool
    async def list_channels() -> str:
        """List all available communication channels.
        Returns channel IDs the agent can use with send_to_channel."""
        ids = router.get_channel_ids()
        if not ids:
            return "No channels registered."
        descriptions = router.get_channel_descriptions()
        parts = []
        for cid in ids:
            label = descriptions.get(cid)
            parts.append(f"{cid} ({label})" if label else cid)
        return ", ".join(parts)

    @function_tool
    async def send_to_channel(channel_id: str, text: str) -> str:
        """Send a message to the user via a specific channel.

        Use when the user explicitly asks to communicate through a particular channel
        (e.g. "send to Telegram", "напиши мне в Slack").

        Args:
            channel_id: Channel ID from list_channels (e.g. "telegram_channel").
            text: Message to deliver.
        """
        if channel_id not in router.get_channel_ids():
            return f"Error: channel '{channel_id}' not found. Use list_channels to see available channels."
        await router.notify_user(text, channel_id)
        return f"Message sent to {channel_id}."

    return [list_channels, send_to_channel]
```

Design notes:
- `send_to_channel` validates the `channel_id` up front and returns a clear error if the channel is not registered, instead of silently falling back to the default channel.
- It delegates to `router.notify_user(text, channel_id)` — single source of truth for delivery logic (user_id resolution, channel selection). The tool has no knowledge of user identities.
- `list_channels` returns human-readable output like `"cli_channel (CLI Channel), telegram_channel (Telegram Channel)"` — the labels come from manifest `name` fields, injected via `router.set_channel_descriptions()` during bootstrap. This helps the LLM map natural language ("Telegram") to the correct `channel_id` without requiring exact ID knowledge.

### 6. Inject channel tools into Orchestrator

Channel tools are created by the runner (which has access to the router) and passed into the Orchestrator at creation time, alongside existing core tools and extension tools.

```python
# core/runner.py — during bootstrap, after router is created

from core.tools.channel import make_channel_tools

channel_tools = make_channel_tools(router)

agent = create_orchestrator_agent(
    model_router=model_router,
    extension_tools=loader.get_all_tools(),
    agent_tools=loader.get_agent_tools(),
    capabilities_summary=loader.get_capabilities_summary(),
    channel_tools=channel_tools,          # NEW
)
```

`create_orchestrator_agent` merges them into the tools list like other core tools. This keeps the Orchestrator construction explicit and testable.

### 7. Scheduler integration (already works)

No scheduler changes needed. The infrastructure is ready:

1. Agent calls `schedule_once(topic="system.user.notify", payload_json='{"text": "Выключи чайник!", "channel_id": "telegram_channel"}', delay_seconds=300)`.
2. Scheduler stores the payload as-is in SQLite.
3. On tick, scheduler emits `system.user.notify` with the stored payload.
4. Kernel handler `on_user_notify` reads `channel_id` from the payload → `router.notify_user(text, "telegram_channel")`.
5. Router selects the Telegram channel, resolves `user_id` via `get_default_user_id()`, delivers.

The docstrings in `schedule_once` and `schedule_recurring` already show `channel_id` in payload examples. After the `user_id` fix (item 3 above), this path will work end-to-end.

### 8. Heartbeat integration

Two options were considered:

**Option A: Add `channel_id` to `HeartbeatDecision`.**
The Scout would need to extract `channel_id` from memory context and include it in the structured output. This couples the heartbeat schema to channel routing and requires the Scout LLM to reliably extract metadata.

**Option B: Let the Orchestrator choose the channel.**
When heartbeat escalates via `ctx.request_agent_task(reason)`, the Orchestrator receives the full context (including memory with channel preferences). The Orchestrator already has `send_to_channel` — it can reason about where to deliver the result.

**Chosen: Option B.** The Orchestrator is the decision-maker. The heartbeat stays a lightweight triage agent. If a task in memory contains channel preference (e.g. "remind in Telegram"), the Orchestrator can see it and act accordingly.

No changes to `HeartbeatDecision` schema are needed. However, the Scout's `prompt.jinja2` should be updated to instruct it to **include channel context in escalation reasons** when the memory contains an explicit channel preference. For example, instead of `reason: "Pending reminder: remind user about kettle"`, the Scout should produce `reason: "Pending reminder (channel: telegram_channel): remind user about kettle"`. This is a prompt-level nudge, not a schema change — it makes the Orchestrator's job easier without coupling the Scout to channel routing.

```
# Addition to heartbeat/prompt.jinja2

When escalating, if the task in memory mentions a specific channel (e.g. "telegram_channel"),
include it in the reason so the orchestrator knows where to deliver.
```

### 9. System prompt guidance

The Orchestrator's system prompt (or capabilities summary) should include guidance about channel tools. This can be added to the prompt template or appended via `capabilities_summary`:

```
## Communication Channels

You can send messages to specific channels when the user asks.
- Use `list_channels()` to see available channels.
- Use `send_to_channel(channel_id, text)` to send a message to a specific channel.
- When scheduling reminders for a specific channel, include "channel_id" in the
  payload_json of schedule_once / schedule_recurring.
- If the user doesn't specify a channel, respond normally (default channel).
```

## Scenario Walkthroughs

### Scenario 1: "Передай мне привет в Telegram"

```
CLI → user.message{text, user_id="cli_user", channel_id="cli_channel"}
  → kernel_user_message_handler → router.handle_user_message(text, "cli_user", cli_channel)
  → Orchestrator runs:
      1. tool call: send_to_channel("telegram_channel", "Привет")
         → router.notify_user("Привет", "telegram_channel")
                              ┌─────────────────────────────────────────────┐
                              │ Inside router (invisible to agent):         │
                              │   ch = _channels["telegram_channel"]        │
                              │   uid = ch.get_default_user_id() → chat_id  │
                              │   ch.send_to_user(uid, "Привет")            │
                              └─────────────────────────────────────────────┘
         → Telegram delivers "Привет"
         → tool returns: "Message sent to telegram_channel."
      2. Orchestrator returns: "Хорошо, передал привет в Telegram"
  → cli_channel.send_to_user("cli_user", "Хорошо, передал привет в Telegram")
```

### Scenario 2: "Через 5 минут напомни мне в Telegram выключить чайник"

```
CLI → user.message → Orchestrator runs:
    tool call: schedule_once(
        topic="system.user.notify",
        payload_json='{"text": "Выключи чайник!", "channel_id": "telegram_channel"}',
        delay_seconds=300
    )
    Orchestrator returns: "Запомнил, напомню через 5 минут в Telegram"

[5 minutes later]
  Scheduler tick → fetch_due_one_shot → row{topic="system.user.notify", payload=...}
  → ctx.emit("system.user.notify", {"text": "Выключи чайник!", "channel_id": "telegram_channel"})
  → EventBus → on_user_notify handler
  → router.notify_user("Выключи чайник!", "telegram_channel")
    → (router resolves user_id internally via channel, delivers)
  → [Telegram] "Выключи чайник!"
```

### Scenario 3: Heartbeat escalation with channel context

```
Heartbeat Scout → enriched prompt includes memory:
    "Task: remind user about kettle in Telegram (channel: telegram_channel)"
  → HeartbeatDecision{action="escalate", reason="Pending reminder: ..."}
  → ctx.request_agent_task("Pending reminder: user asked to be reminded in Telegram about kettle")
  → system.agent.task → kernel → router.invoke_agent(prompt)
  → Orchestrator sees "Telegram" in prompt, calls:
      tool: send_to_channel("telegram_channel", "Напоминание: выключи чайник!")
  → Telegram delivers
```

## Implementation Plan

### Phase 1: MVP (this ADR)

| # | Component | Change | Scope |
|---|-----------|--------|-------|
| 1 | `ChannelProvider` protocol | Add `get_default_user_id() -> str` | `core/extensions/contract.py` |
| 2 | `cli_channel` | Implement `get_default_user_id()` | `sandbox/extensions/cli_channel/main.py` |
| 3 | `telegram_channel` | Implement `get_default_user_id()` | `sandbox/extensions/telegram_channel/main.py` |
| 4 | `MessageRouter` | Add `get_channel_ids()`, `get_channel_descriptions()`, `set_channel_descriptions()`; fix `notify_user()` user_id resolution | `core/extensions/router.py` |
| 5 | Channel tools | New module: `list_channels` (with human-readable names), `send_to_channel` (with validation) | `core/tools/channel.py` |
| 6 | Orchestrator bootstrap | Pass channel tools at agent creation; call `set_channel_descriptions()` from Loader | `core/runner.py`, `core/agents/orchestrator.py` |
| 7 | System prompt | Add channel guidance to orchestrator prompt | Prompt template |
| 8 | Heartbeat prompt | Instruct Scout to include channel context in escalation reasons | `sandbox/extensions/heartbeat/prompt.jinja2` |

Estimated complexity: **Low.** All changes are additive. No schema migrations, no new dependencies, no protocol-breaking changes.

### Phase 2: Multi-channel awareness (future)

- **Channel metadata:** `ChannelProvider.get_channel_info() -> dict` returning capabilities (supports images, max message length, formatting options). Agent can make smarter choices.
- **User preferences:** Persist per-user channel preferences in memory. Agent defaults to preferred channel without being asked.
- **Broadcast:** `send_to_all_channels(text)` tool for announcements.
- **Sub-agent access:** Document that sub-agents (e.g. `builder_agent`) can receive channel tools by listing `channel_tools` in their manifest `uses_tools`. Phase 1 gives channel tools only to the Orchestrator; specialized agents that need to notify the user on a specific channel should explicitly opt in.

### Phase 3: Multi-user (future)

- **User registry:** Map user identities across channels (same person on CLI and Telegram).
- **User-scoped routing:** `send_to_channel(channel_id, text, user_id)` — explicit user targeting.
- **Channel session:** Track which channel each conversation is happening on; maintain per-channel conversation history.

## Consequences

### Benefits

- **Agent autonomy:** The agent can choose the delivery channel based on user intent, schedule context, or its own judgment.
- **Minimal changes:** Leverages existing `channel_id` plumbing through EventBus, kernel handlers, and router.
- **Extensible:** New channels automatically appear in `list_channels()` after registration — no code changes needed.
- **Consistent with architecture:** Tools are the agent's interface to the world (ADR 002, ADR 003). Channel selection becomes just another tool.

### Trade-offs

| Trade-off | Impact |
|-----------|--------|
| **Core tools grow by 2** | Acceptable; tools are small and focused |
| **Protocol extension** | `get_default_user_id()` is additive; `hasattr` guard provides backward compat |
| **Single-user assumption** | Phase 1 only; acceptable for current deployment |
| **LLM reliability** | Agent must correctly choose `channel_id` from natural language; mitigated by small option set and clear tool descriptions |

### Risks

| Risk | Severity | Mitigation |
|------|----------|-------------|
| **Agent picks wrong channel** | Low | `list_channels` shows available options; user can correct |
| **Channel offline** | Low | `send_to_user` already handles errors; agent receives error from tool |
| **Prompt injection via channel_id** | Low | Router validates `channel_id` against registered channels; unknown IDs rejected |

## Alternatives Considered

### Event-based channel selector (no tools)

Introduce a `system.channel.select` event that the agent emits before sending a message. A kernel handler resolves the channel and forwards.

**Rejected.** Adds indirection without benefit. Tools are the natural LLM interface; events are for decoupled, asynchronous flows.

### Middleware-based channel resolution

A `ContextProvider` that detects channel intent in the user's message and injects `target_channel_id` into the agent context. Agent doesn't need tools — it just includes the channel in `schedule_once` payloads.

**Rejected.** Fragile NLP heuristic. The agent is already an LLM — let it reason about channels explicitly via tools.

### `HeartbeatDecision.channel_id` field

Add `channel_id: str | None` to `HeartbeatDecision` so the Scout can specify where to deliver escalations.

**Deferred.** Adds complexity to the Scout's structured output for marginal benefit. The Orchestrator already has the context and tools to make the decision. Can be revisited if Orchestrator consistently fails to select the right channel on escalation.

## Relation to Other ADRs

- **ADR 002 (Nano-Kernel)** — Channel tools follow the "all functionality in extensions/tools" principle. `get_channel_ids()` and `get_default_user_id()` are minimal additions to core contracts.
- **ADR 003 (Agent-as-Extension)** — Sub-agents can access channel tools if included in their `uses_tools`. For Phase 1, only the Orchestrator receives them. Phase 2 should document how sub-agents opt in via manifest, so developers adding new agents know the capability exists.
- **ADR 004 (Event Bus)** — No EventBus changes. The existing `system.user.notify` and `system.agent.task` payloads already carry `channel_id`.
- **ADR 006 (MCP Extension)** — MCP tools and channel tools coexist. An MCP server could theoretically expose channel-like tools, but native channel tools are preferred for reliability and protocol integration.

## References

- ADR 002: Nano-Kernel + Extensions
- ADR 003: Agent-as-Extension
- ADR 004: Event Bus in Core
- `core/extensions/router.py` — MessageRouter implementation
- `core/extensions/contract.py` — ChannelProvider protocol
- `core/extensions/context.py` — ExtensionContext API
- `sandbox/extensions/scheduler/main.py` — Scheduler tools and tick loop
- `sandbox/extensions/heartbeat/main.py` — HeartbeatDecision and escalation
