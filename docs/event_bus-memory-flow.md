# EventBus → MessageRouter → Memory Flow

This document describes the data flow from user input through EventBus and MessageRouter into the Memory extension, including context injection and consolidation. It aligns with [ADR 005: Simplified Memory System](adr/005-memory.md) and reflects the current implementation in `sandbox/extensions/memory` and `sandbox/extensions/memory_consolidator`.

---

## Overview

Memory integrates via **two subscription mechanisms**:

| Source | API | Backend | Purpose |
|--------|-----|---------|---------|
| MessageRouter | `context.subscribe(event, handler)` | In-memory `router._emit()` | User messages and agent responses (hot path) |
| EventBus | `context.emit(topic, payload)` | SQLite journal | Consolidation triggers (`memory.session_completed`) |

**Important:** The Memory extension does **not** subscribe to EventBus `user.message` directly. User messages reach Memory indirectly: EventBus → Loader kernel handler → MessageRouter → Memory (via MessageRouter internal events).

---

## 1. User Message Flow (EventBus → MessageRouter → Memory)

```
┌─────────────────┐     ctx.emit()     ┌─────────────────────────────────────────────┐
│ Channel         │ ─────────────────► │ EventBus (SQLite journal)                   │
│ (cli_channel)   │  "user.message"    │ topic: user.message                         │
│                 │  {text, user_id,   │ payload: {text, user_id, channel_id}        │
└─────────────────┘   channel_id}      └──────────────────────┬──────────────────────┘
                                                              │ dispatch loop
                                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────┐
│ Loader: kernel_user_message_handler (subscribes to EventBus "user.message")        │
│   → router.handle_user_message(text, user_id, channel)                             │
└────────────────────────────────────────────────────────────────────────────────────┘
                                                               │
                                                               ▼
┌────────────────────────────────────────────────────────────────────────────────────┐
│ MessageRouter.handle_user_message(text, user_id, channel)                          │
│                                                                                    │
│   1. _emit("user_message", {text, user_id, channel, session_id})  ──► Memory       │
│   2. invoke_agent(text)  [with ContextProvider middleware: Memory.get_context()]   │
│   3. _emit("agent_response", {text, agent_id, ...})  ──► Memory                    │
│   4. channel.send_to_user(user_id, response)                                       │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### Step-by-step

1. **Channel emits** — `cli_channel` (or any ChannelProvider) receives user input and publishes to EventBus:
   ```python
   await context.emit("user.message", {"text": line, "user_id": "cli_user", "channel_id": extension_id})
   ```

2. **EventBus persists** — Event is written to `event_journal` (SQLite), then delivered by the dispatch loop to all subscribers.

3. **Kernel handler** — Loader registers `kernel_user_message_handler` on EventBus topic `user.message` (in `wire_event_subscriptions()`). The handler calls `router.handle_user_message(text, user_id, channel)`.

4. **MessageRouter** — `handle_user_message()`:
   - Emits `user_message` to MessageRouter subscribers (in-memory, synchronous with the call)
   - Invokes the agent (with context injection middleware)
   - Emits `agent_response` to MessageRouter subscribers
   - Sends the response to the user via the channel

5. **Memory ingestion** — Memory subscribes in `initialize()`:
   ```python
   context.subscribe("user_message", self._on_user_message)
   context.subscribe("agent_response", self._on_agent_response)
   ```
   Both handlers call `save_episode()` → `MemoryRepository.save_episode()` → `memories` table (kind='episode'), FTS5 indexed via DB triggers. **Hot path: no LLM, no embedding** (ADR §6).

---

## 2. Context Injection (Memory → Agent)

Before each agent invocation, the Loader wires a **ContextProvider** middleware chain into the MessageRouter. Memory implements `ContextProvider` with `context_priority=100`.

```
MessageRouter.invoke_agent(prompt)
    │
    ▼
set_invoke_middleware() chain
    │
    ├─► Memory.get_context(prompt)  →  fts_search(prompt, limit=5)
    │       → "## Relevant memory\n- ...\n- ..."
    │
    ▼
Enhanced prompt = header + "\n\n---\n\n" + original_prompt
    │
    ▼
Runner.run(agent, enhanced_prompt, session=session)
```

- **Where:** `Loader.wire_context_providers()` builds the middleware; `router.set_invoke_middleware()` registers it.
- **Memory role:** `get_context()` returns FTS5 search results as a formatted string, or `None` if no matches. Excludes the current session to avoid redundant context.

---

## 3. Consolidation Flow (Memory → EventBus → Memory Consolidator)

Consolidation extracts semantic facts from episodes. It is triggered in two ways:

### 3.1 Session switch (reactive)

When the user starts a new session, Memory emits `memory.session_completed` for all **pending** (non-current) sessions:

```python
# Memory._on_user_message
if session_id and session_id != self._current_session_id:
    self._current_session_id = session_id
    await self._repo.ensure_session(session_id)
    await self._trigger_consolidation(session_id)  # emits for pending sessions
```

`_trigger_consolidation()`:
```python
for session_id in pending:
    await self._ctx.emit("memory.session_completed", {"session_id": session_id, "prompt": "..."})
```

### 3.2 Scheduler (nightly)

`memory_consolidator` implements `SchedulerProvider` with cron `0 3 * * *` (03:00 daily). On tick:

```python
# MemoryConsolidatorExtension.execute()
pending = await mem.get_all_pending_consolidations()
for session_id in pending:
    await self._ctx.emit("memory.session_completed", {...})
```

### 3.3 EventBus → Consolidator Agent

`memory_consolidator` manifest declares:

```yaml
events:
  subscribes:
    - topic: memory.session_completed
      handler: invoke_agent
```

The Loader wires this as a **proactive** EventBus subscription: when `memory.session_completed` is published, the consolidator agent is invoked with the event payload as the task prompt.

```
EventBus "memory.session_completed"
    │
    ▼
Loader proactive_handler (invoke_agent)
    │
    ▼
MemoryConsolidatorExtension.invoke(task, context)
    │
    ▼
Runner.run(agent, task)  # agent uses memory consolidator tools
    │
    ├─ get_episodes_for_consolidation(session_id)
    ├─ save_facts_batch(session_id, facts)
    └─ mark_session_consolidated(session_id)
```

The consolidator agent uses tools from the Memory extension (`get_consolidator_tools()`): `get_episodes_for_consolidation`, `save_facts_batch`, `mark_session_consolidated`, `is_session_consolidated`. These tools are **not** exposed to the Orchestrator.

---

## 4. Data Flow Summary

| Stage | Component | Action |
|-------|-----------|--------|
| **Ingestion** | Channel | `emit("user.message", {...})` → EventBus |
| | EventBus | Journal + dispatch → kernel handler |
| | Loader | `router.handle_user_message()` |
| | MessageRouter | `_emit("user_message")` → Memory |
| | Memory | `_on_user_message` → `save_episode` |
| | MessageRouter | `invoke_agent` (with context) |
| | MessageRouter | `_emit("agent_response")` → Memory |
| | Memory | `_on_agent_response` → `save_episode` |
| **Retrieval** | MessageRouter | `invoke_middleware` → Memory.get_context |
| | Memory | `fts_search` → formatted string |
| **Consolidation** | Memory / Scheduler | `emit("memory.session_completed", {...})` → EventBus |
| | EventBus | Dispatch → proactive handler |
| | Loader | Invoke memory_consolidator agent |
| | Consolidator | Tools → MemoryRepository (save_facts_batch, mark_session_consolidated) |

---

## 5. Implementation Notes

### ADR vs current implementation

ADR 005 §11 states that Memory subscribes to:
- `user.message` via `context.subscribe_event()` (EventBus)
- `agent_response` via `context.subscribe()` (MessageRouter)

**Current implementation:** Memory subscribes only via `context.subscribe()` to MessageRouter events `user_message` and `agent_response`. User messages reach Memory **indirectly** through the kernel handler → `handle_user_message` → `_emit("user_message")`. This achieves the same hot-path behavior (no direct EventBus subscription for user messages) and keeps the flow simpler: one kernel handler routes all `user.message` events into MessageRouter, and Memory receives from MessageRouter.

### Hot path constraints (ADR §6)

- `save_episode` → INSERT + FTS5 (via triggers). No LLM, no embedding in the hot path.
- Embeddings and entity extraction run in background (Phase 2+).

### Extension dependencies

- `memory_consolidator` depends on `memory` (manifest `depends_on`).
- Consolidator gets tools via `context.get_extension("memory").get_consolidator_tools()`.

---

## References

- [ADR 005: Simplified Memory System](adr/005-memory.md)
- [Event Bus](event_bus.md)
- [Extensions](extensions.md)
