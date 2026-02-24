# EventBus → MessageRouter → Memory Flow

This document describes the data flow from user input through EventBus and MessageRouter into the Memory v2 extension, including context injection, consolidation, and nightly maintenance. It aligns with [ADR 008: Memory v2](adr/008-memory-v2.md) and reflects the current implementation in `sandbox/extensions/memory`.

---

## Overview

Memory integrates via **two subscription mechanisms**:

| Source | API | Backend | Purpose |
|--------|-----|---------|---------|
| MessageRouter | `context.subscribe(event, handler)` | In-memory `router._emit()` | User messages and agent responses (hot path) |
| EventBus | `context.subscribe_event(topic, handler)` | SQLite journal | Session lifecycle (`session.completed`) |

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
│   1. Check session timeout → _rotate_session() if inactivity exceeded              │
│   2. _emit("user_message", {text, user_id, channel, session_id})  ──► Memory       │
│   3. If channel is StreamingChannelProvider:                                       │
│        channel.on_stream_start → invoke_agent_streamed(on_chunk, on_tool_call)      │
│        → channel.on_stream_end(full_text)                                           │
│      Else: invoke_agent(text) → channel.send_to_user(user_id, response)             │
│   4. _emit("agent_response", {text, agent_id, ...})  ──► Memory                    │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### Step-by-step

1. **Channel emits** — `cli_channel` (or any ChannelProvider) receives user input and publishes to EventBus:
   ```python
   await context.emit("user.message", {"text": line, "user_id": "cli_user", "channel_id": extension_id})
   ```

2. **EventBus persists** — Event is written to `event_journal` (SQLite), then delivered by the dispatch loop to all subscribers.

3. **Kernel handler** — Loader registers `kernel_user_message_handler` on EventBus topic `user.message`. The handler calls `router.handle_user_message(text, user_id, channel)`.

4. **Session timeout** — `MessageRouter` checks if `(now - _last_message_at) > session_timeout`. If exceeded, it rotates the session: generates a new session ID, publishes `session.completed` via EventBus for the old session.

5. **MessageRouter** — `handle_user_message()`:
   - Emits `user_message` to MessageRouter subscribers (in-memory, synchronous)
   - If the channel implements `StreamingChannelProvider`, uses the streaming path: `on_stream_start` → `invoke_agent_streamed()` (callbacks push chunks and tool status to the channel) → `on_stream_end`. Otherwise invokes the agent and calls `channel.send_to_user()`.
   - Emits `agent_response` to MessageRouter subscribers (with full response text in both paths)
   - Response is already delivered by the channel (streaming or non-streaming)

6. **Memory ingestion** — Memory subscribes in `initialize()`:
   ```python
   context.subscribe("user_message", self._on_user_message)
   context.subscribe("agent_response", self._on_agent_response)
   ```
   Both handlers:
   - Create an episodic node (fire-and-forget via writer queue)
   - Create a temporal edge to the previous episode (fire-and-forget)
   - Schedule slow-path embedding generation (`asyncio.create_task`)
   - On session change: trigger consolidation of the old session

---

## 2. Context Injection (Memory → Agent)

Before each agent invocation, the Loader wires a **ContextProvider** middleware chain into the MessageRouter. Memory implements `ContextProvider` with `context_priority=50`.

```
MessageRouter.invoke_agent(prompt)
    │
    ▼
set_invoke_middleware() chain
    │
    ├─► Memory.get_context(prompt)
    │       → classify_query_complexity(prompt)
    │       → embed_fn(prompt)                     [if embedding available]
    │       → intent_classifier.classify(prompt)
    │       → hybrid search: FTS5 + vector + graph → RRF fusion
    │       → assemble_context(results, token_budget)
    │              Facts 40% | Entity profiles 25% | Temporal 25% | Evidence 10%
    │       → return formatted markdown or None
    │
    ▼
context = middleware(prompt)  →  empty string if no matches
    │
    ▼
If context non-empty and agent.instructions is str:
    agent = agent.clone(instructions=agent.instructions + "\n\n---\n\n" + context)
    │
    ▼
Runner.run(agent, prompt, session=session)
    → API: system = base instructions + context; user = prompt (unchanged)
```

- **Where:** `Loader.wire_context_providers()` builds the middleware; `router.set_invoke_middleware()` registers it.
- **Memory role:** `get_context()` runs intent-aware hybrid search and returns structured context with budget-allocated sections, or `None` if no matches.
- **Context injection:** Context goes into the **system** role via `agent.clone(instructions=...)`, not into the user message.

---

## 3. Consolidation Flow

Consolidation extracts semantic facts, procedural patterns, and opinions from episodic nodes. It is triggered in two ways:

### 3.1 Session change (reactive)

When the user starts a new session (either by session ID change or inactivity timeout), Memory triggers consolidation of the old session:

```python
# Memory._on_user_message
if session_id and session_id != self._current_session_id:
    if self._current_session_id:
        asyncio.create_task(self._consolidate_session(self._current_session_id))
    self._current_session_id = session_id
    self._storage.ensure_session(session_id)
```

### 3.2 Session rotation (MessageRouter)

When inactivity exceeds `session.timeout_sec` (default 1800s), MessageRouter rotates the session and publishes `session.completed`:

```python
# MessageRouter.handle_user_message
if (now - _last_message_at) > _session_timeout:
    _rotate_session()  # publishes session.completed via EventBus
```

Memory subscribes to `session.completed` via `context.subscribe_event()` and triggers consolidation.

### 3.3 Nightly maintenance (scheduled)

The `memory` extension implements `SchedulerProvider`. At 03:00 daily:

```python
# MemoryExtension.execute_task("run_nightly_maintenance")
unconsolidated = await self._storage.get_unconsolidated_sessions()
for sid in unconsolidated:
    await self._consolidate_session(sid)
```

### 3.4 Write-path agent execution

```
_consolidate_session(session_id)
    │
    ▼
MemoryAgent.consolidate_session(session_id)
    │
    ▼
Runner.run(agent, task)  # agent uses write-path tools
    │
    ├─ is_session_consolidated(session_id)          → skip if true
    ├─ get_session_episodes(session_id, paginated)
    ├─ [LLM] extract facts, procedures, opinions
    ├─ save_nodes_batch(nodes + source_episode_ids)  → derived_from edges + batch embed
    ├─ extract_and_link_entities(nodes + entities)   → resolve or create entity anchors
    ├─ detect_conflicts(fact) → resolve_conflict(old, new) if needed
    └─ mark_session_consolidated(session_id)
```

The write-path agent is a private `Agent` instance inside the memory extension (not an `AgentProvider`). Its tools are internal and not exposed to the Orchestrator.

---

## 4. Nightly Maintenance Pipeline

Beyond consolidation, the nightly task runs three additional stages:

```
execute_task("run_nightly_maintenance")
    │
    ├─ 1. Consolidate pending sessions (see §3.3)
    │
    ├─ 2. Ebbinghaus decay + pruning
    │     → DecayService.apply(storage)
    │     → confidence_new = confidence × exp(−decay_rate × days^0.8)
    │     → batch_update_confidence / soft_delete_nodes
    │
    ├─ 3. Entity enrichment
    │     → get_entities_needing_enrichment(min_mentions=3)
    │     → for each sparse entity: LLM generates summary
    │     → update_entity_summary + re-embed
    │
    └─ 4. Causal edge inference
          → get_consecutive_episode_pairs(limit=50)
          → MemoryAgent.infer_causal_edges(pairs)
          → [LLM] analyze pairs for explicit cause-effect
          → save_causal_edges (confidence=0.7)
```

---

## 5. Data Flow Summary

| Stage | Component | Action |
|-------|-----------|--------|
| **Ingestion** | Channel | `emit("user.message", {...})` → EventBus |
| | EventBus | Journal + dispatch → kernel handler |
| | Loader | `router.handle_user_message()` |
| | MessageRouter | `_emit("user_message")` → Memory |
| | Memory | `_on_user_message` → episodic node + temporal edge + slow path |
| | MessageRouter | `invoke_agent` (with context) |
| | MessageRouter | `_emit("agent_response")` → Memory |
| | Memory | `_on_agent_response` → episodic node + temporal edge + slow path |
| **Retrieval** | MessageRouter | `invoke_middleware` → Memory.get_context |
| | Memory | Intent classification → hybrid search → RRF fusion → context assembly |
| **Consolidation** | Memory / Scheduler | `_consolidate_session()` → write-path agent |
| | MemoryAgent | Tools → MemoryStorage (save_nodes_batch, extract_and_link_entities, mark_session_consolidated) |
| **Maintenance** | Scheduler (03:00) | Consolidate + decay + entity enrichment + causal inference |

---

## 6. Implementation Notes

### ADR 005 → ADR 008 migration

Memory v2 (ADR 008) replaces the v1 system entirely:

- Flat `memories` table → graph schema (`nodes` + `edges` + `entities`)
- Satellite extensions (`memory_maintenance`, `memory_reflection`, `ner`) → single `memory` extension
- `ToolProvider` + `ContextProvider` → `ToolProvider` + `ContextProvider` + `SchedulerProvider`
- Intent-blind hybrid search → intent-aware retrieval with graph traversal
- External consolidation agent → internal write-path agent

### Hot path constraints

- Episodic node INSERT + FTS5 trigger + temporal edge. No LLM, no blocking waits.
- Embedding generation runs as a background task (`asyncio.create_task`).

### Writer queue pattern

All writes go through `MemoryStorage._write_queue` (asyncio.Queue) processed by a single writer task. Hot-path writes are fire-and-forget; slow-path and tool writes use awaitable futures.

---

## References

- [ADR 008: Memory v2](adr/008-memory-v2.md)
- [Memory System](memory.md)
- [Event Bus](event_bus.md)
- [Extensions](extensions.md)
