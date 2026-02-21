# ADR 005: Simplified Memory System for AI Agent

## Status

Proposed

## Context

The predecessor project (assistant3) implemented a cognitive memory system, that design synthesized ideas from 10+ SOTA systems (MAGMA, Zep/Graphiti, Hindsight/TEMPR, MemoryOS, FadeMem, A-MEM, Mem0, ES-Mem, Synapse, Better-Memory-MCP) and produced an academically rigorous architecture. However, expert review and practical use revealed significant over-engineering for a single-user, locally-running AI agent:


| Aspect in 003-memory                              | Why it was excessive                                                                                                                                                                                                                                               |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 5 cognitive layers                                | **Session memory** (conversation context within one discussion) is handled by [OpenAI Agents SDK Sessions](https://openai.github.io/openai-agents-python/sessions/sqlalchemy_session/) (SQLAlchemySession). Procedural and Opinion are `fact` with different tags. |
| Edges table (6 relation types)                    | Graph traversal is needed at thousands of entities with multi-dimensional links. A personal agent never reaches that scale.                                                                                                                                        |
| Bi-temporal model (4 time fields)                 | Enterprise data warehouse pattern, not a personal assistant.                                                                                                                                                                                                       |
| Causal inference + Event Segmentation in hot path | LLM on every event = expensive, slow, unstable.                                                                                                                                                                                                                    |
| 13 agent tools                                    | Clutters system prompt; agent gets confused.                                                                                                                                                                                                                       |
| Community detection (label propagation)           | Useful at >100k nodes in enterprise graphs.                                                                                                                                                                                                                        |


Expert feedback and code analysis converged on a simpler, pragmatic design that preserves the valuable ideas while eliminating unnecessary complexity.

## Decision

### 1. Memory Architecture: Session vs Long-term

The memory system is split into two distinct layers. **This ADR covers only long-term memory.** Session memory is out of scope.


| Layer                | Responsibility                                                                                                                                                                                   | Implementation                                                                                                                                                                                             |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Session memory**   | Conversation context within a single user–agent discussion. Retrieves history before each turn, stores new messages after. Enables multi-turn coherence ("What state is it in?" → "California"). | [OpenAI Agents SDK Sessions](https://openai.github.io/openai-agents-python/sessions/sqlalchemy_session/) — `SQLAlchemySession` or `SQLiteSession`. Passed to `Runner.run(agent, prompt, session=session)`. |
| **Long-term memory** | Cross-session persistence: episodes, facts, preferences, entities. Survives restarts. Enables "What did we discuss about Project Alpha last week?"                                               | Memory extension (this ADR) — `memories` + `entities` tables, EventBus subscriptions, hybrid search.                                                                                                       |


**Integration:** The Orchestrator uses SDK Sessions for in-conversation context. The Memory extension injects retrieved long-term context into the system prompt before each agent invocation. The two layers are complementary: Sessions = working context; Memory = durable knowledge.

### 2. Design Principles


| Principle                                   | Rationale                                                                                                                                                          |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Pragmatic over academic**                 | Solve real problems for a single-user agent; avoid solving problems that do not exist. Aligns with "Minimalist SOTA" and "Incremental Knowledge Graph" approaches. |
| **LLM off the hot path**                    | No LLM calls during event ingestion. LLM only at night (consolidation) and on explicit tool calls.                                                                 |
| **Single table by kind**                    | One `memories` table with `kind` field instead of multiple layer-specific tables.                                                                                  |
| **Memory = ServiceProvider + ToolProvider** | Memory does not implement `SchedulerProvider`. Memory owns `consolidate()`; `self_reflection` triggers it on schedule.                                             |
| **EventBus as integration point**           | Memory subscribes to `user.message` (EventBus) and `agent_response` (MessageRouter) via `context.subscribe_event()` and `context.subscribe()`.                     |
| **Context injection**                       | One generic kernel extension point: loader/router calls `search_memory()` before each agent invocation and prepends result to the prompt. See §10.                 |
| **Schema-first, logic-later**               | Deploy full schema from day one; implement only critical paths initially. Enables no-migration evolution.                                                          |


### 3. What to Keep from 003-memory


| Idea                                   | Why it works                                                                     |
| -------------------------------------- | -------------------------------------------------------------------------------- |
| **SQLite + FTS5 + sqlite-vec**         | All in one file, no external dependencies.                                       |
| **Entity Anchors**                     | Without them, "Vitya", "Vitaly", "my boss" are three different people in memory. |
| **Decay (Ebbinghaus)**                 | Formula `confidence × exp(−0.1 × days^0.8)` is elegant and empirically grounded. |
| **Night consolidation**                | LLM only at night; no impact on UX.                                              |
| **Soft-delete**                        | `valid_until = now` instead of physical delete; history preserved.               |
| **Hybrid search: FTS5 + vector + RRF** | Objectively better than any single method.                                       |


### 4. Data Schema

**Single `memories` table** — `kind` determines type. No separate `reflections` table; reflections are `memories (kind='reflection')`. JSON arrays for graph links (no separate `edges` table).

`**event_time` vs `created_at`:** We include `event_time` in the schema for future bi-temporal support (e.g., importing historical emails). In Phase 1 implementation, set `event_time = created_at` — for a personal agent, user writes → we record immediately.

```sql
CREATE TABLE memories (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,      -- episode | fact | preference | reflection
    content      TEXT NOT NULL,
    embedding    BLOB,               -- 256-dim; see Embedding Strategy below

    event_time   INTEGER NOT NULL,   -- when event occurred (Phase 1: = created_at)
    created_at   INTEGER NOT NULL,   -- when recorded in DB (Unix epoch)
    valid_until  INTEGER,            -- NULL = current (soft-delete)

    confidence   REAL DEFAULT 1.0,
    access_count INTEGER DEFAULT 0,
    last_accessed INTEGER,
    decay_rate   REAL DEFAULT 0.1,   -- 0.1 default; 0.0 when protected (entity). Per-node custom rates: Phase 4+.

    source_ids   TEXT DEFAULT '[]',  -- JSON: [memory_id, ...] — provenance (episodes → fact)
    entity_ids   TEXT DEFAULT '[]',  -- JSON: [entity_id, ...]
    tags         TEXT DEFAULT '[]',  -- JSON: ["work","project_alpha"] — fast filter: WHERE tags LIKE '%"work"%'
    attributes   TEXT DEFAULT '{}'   -- extensible metadata (rich structure)
);

CREATE TABLE entities (
    id             TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    type           TEXT NOT NULL,   -- person | project | org | concept | tool
    aliases        TEXT DEFAULT '[]',
    summary        TEXT,
    embedding      BLOB,
    mention_count  INTEGER DEFAULT 1,
    protected      INTEGER DEFAULT 0  -- =1: decay not applied
);

-- FTS5 + sqlite-vec as virtual tables
CREATE VIRTUAL TABLE memories_fts USING fts5(content, content=memories, content_rowid=rowid, tokenize='unicode61');
CREATE VIRTUAL TABLE vec_memories USING vec0(memory_id TEXT PRIMARY KEY, embedding float[256]);
```

**Embedding strategy:** 256 dimensions from OpenAI `text-embedding-3-large` with native Matryoshka dimensionality reduction. Optimal storage/performance balance; retains ~95% of full-model quality at 1/12th storage cost.

**Decay rate rule:** By default, `decay_rate` is not customized per-node — it is either 0.1 (default) or 0.0 (when entity has `protected=1`). Per-node custom rates are reserved for Phase 4+; avoid tuning in Phase 1–3.

**No `edges` table.** Relationships expressed via:

- `source_ids` — JSON array of source memory IDs. A fact can be extracted from multiple episodes during consolidation; `source_ids` stores all provenance. Example:
  ```sql
  -- Fact "Vitaly prefers concise answers" extracted from 3 episodes:
  source_ids = '["ep_001", "ep_047", "ep_112"]'
  ```
- `entity_ids` — JSON array of entity references. **Phase 2+:** Add `memory_entities` junction table for efficient entity-based search; `entity_ids` JSON remains for serialization in agent tools.

**Phase 2 (or when `memories` > 10k rows):** Add junction table to avoid `WHERE entity_ids LIKE '%"uuid"%'` (anti-pattern, full scan):

```sql
CREATE TABLE memory_entities (
    memory_id  TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (memory_id, entity_id)
);
CREATE INDEX idx_me_entity ON memory_entities(entity_id);
```

Query "all memories about Project X" becomes: `SELECT m.* FROM memories m JOIN memory_entities me ON me.memory_id = m.id WHERE me.entity_id = ?`. Indexed, fast.

### 5. Agent Tools (5 core + 2 optional)

```python
search_memory(query, kind=None, tag=None, limit=10)
# Hybrid: FTS5 + vector + RRF. tag filters by tags (e.g. tag="work"). Primary tool.

remember_fact(content, confidence=1.0, entity_names=[])
# Agent explicitly saves an important fact.

correct_fact(memory_id, new_content)
# Supersede: valid_until=now on old, create new. Agent finds memory_id via search_memory first.

confirm_fact(memory_id)
# protected=1, decay_rate=0 — permanently in memory.

memory_stats()
# Counts: episodes, facts, last consolidation time.

get_entity_info(name)  # optional
# Profile of entity: facts + episodes. For "Tell me everything about Project Alpha".

memory_consolidate()   # optional, for manual trigger
# Run decay + extract + reflect + prune. Normally called by self_reflection on schedule.
# Use sparingly: makes multiple LLM calls, takes 10–60s depending on episode count.
```

### 6. Hot Path (synchronous, <50ms)

```
user.message → save episode → FTS5 indexing
agent.response → save episode → FTS5 indexing
(no LLM, no embedding in hot path)
```

### 7. Background Path (async task, seconds later)

```
generate_embedding(content) → update vec_memories
extract_entities_regex() → resolve_or_create_entity() → update entity_ids
(regex/heuristics only; no LLM)
```

### 8. Night Consolidation (Memory owns logic; self_reflection triggers)

**Consolidation logic lives inside the Memory extension.** Memory exposes `consolidate()` (or `memory_consolidate()` as a callable method). The `self_reflection` extension implements `SchedulerProvider` and, on its cron tick (e.g. 01:00), calls `memory_ext.consolidate()`. Memory is **fully autonomous** — it contains all logic; self_reflection only triggers it on schedule.

```
01:00 (self_reflection.execute()) →
  memory_ext.consolidate() →
    decay()       — Ebbinghaus on all fact/preference
    extract()     — LLM: extract facts from episodes over N days
    conflict()   — LLM prompt: detect contradictions against existing facts (same entity)
    reflect()     — LLM: what did the agent learn this week? → kind='reflection'
    prune()       — soft-delete confidence < 0.05
```

**Conflict detection:** The `consolidate()` prompt explicitly instructs the LLM: *"For each new fact, find direct contradictions in existing facts (same entity, opposite claim). If found: set old fact's confidence to 0.3; add to new fact's attributes: {supersedes: old_memory_id}."* This covers common cases (user changed job, moved, changed preference) without online LLM calls — done once at night.

**If self_reflection is not implemented:** Memory can expose `memory_consolidate()` as an agent tool. The user or another scheduler can trigger it manually. Alternatively, Memory could run a minimal internal consolidation (e.g. decay + prune only, no LLM) on a background timer — but the preferred design is: consolidation method in Memory, trigger from self_reflection.

### 9. Entity Extraction

- **Hot path:** regex only — capitalized names, URLs, dates, hashtags. No spaCy, no LLM.
- **Background:** LLM for entity resolution only when `confidence < 0.7`; not online.

### 10. Context Injection

Context injection **requires a small, documented kernel change**. The principle "no special kernel code" means no Memory-specific logic scattered across the codebase — we add **one generic extension point**.

**Where:** In `MessageRouter.invoke_agent()` (or equivalent), before `Runner.run(agent, prompt)`:

```python
# Loader (at wiring): if memory extension exists, wrap agent invocation
mem = loader._extensions.get("memory")
if mem and hasattr(mem, "search_memory"):
    async def enhanced_invoke(prompt: str) -> str:
        ctx = await mem.search_memory(prompt, limit=5)
        enhanced = f"Relevant context from memory:\n{ctx}\n\n---\n\n{prompt}" if ctx else prompt
        result = await Runner.run(agent, enhanced)
        return result.final_output or ""
    # Wire enhanced_invoke into the invocation path (replaces direct Runner.run)
```

**Chosen approach:** Loader wraps the agent invocation with a pre-invocation step that fetches memory context and prepends to the prompt. The loader checks for the memory extension at wiring time and, if present, injects the enhancer into the invocation path (e.g., via MessageRouter or a wrapper around `Runner.run`). No ambiguity — one implementation path.

The Memory extension only exposes `search_memory(query, limit=5)`. It does not know about injection — the kernel (loader) performs it. This is the **only** kernel integration point for memory.

### 11. Event Subscriptions

Memory uses **two different subscription mechanisms** (different APIs, different backends):


| Event            | API                                                | Backend                               | Purpose                     |
| ---------------- | -------------------------------------------------- | ------------------------------------- | --------------------------- |
| `user.message`   | `context.subscribe_event("user.message", handler)` | **EventBus** (durable journal)        | User messages from channels |
| `agent_response` | `context.subscribe("agent_response", handler)`     | **MessageRouter** (in-memory `_emit`) | Agent responses             |


**Important:** `agent_response` is **not** an EventBus topic. It is a MessageRouter callback — `router.subscribe("agent_response", ...)`. The `subscribe_event()` method goes to EventBus; `subscribe()` goes to MessageRouter. Both are available on `ExtensionContext` (see `context.py`).

Both handlers call `save_episode(content)` in the hot path.

## Implementation Phases


| Phase            | Scope                                                                                                                                   | Outcome                                                             |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| **1 (1 day)**    | Schema, EventBus/MessageRouter subscriptions, FTS5 search                                                                               | Agent remembers conversations across sessions                       |
| **2 (2–3 days)** | sqlite-vec embeddings, hybrid search with RRF, regex entity extraction; add `memory_entities` junction table when `memories` > 10k rows | Semantic search; entities not duplicated; fast entity-based queries |
| **3 (1–2 days)** | Decay formula, soft-delete, consolidate() (self_reflection triggers)                                                                    | Memory self-organizes; night reflection works                       |
| **4 (optional)** | LLM entity resolution when confidence < 0.7                                                                                             | Higher-quality entity anchors                                       |


**Phase 1 approach:** Deploy full schema from day one (no-migration evolution, as in 003-memory). Initially use only `content`, `kind`, `event_time`, `created_at` (with `event_time = created_at`). Fill remaining columns as phases progress.

**Pre-Phase 1 check:** Verify in `core/extensions/context.py` that both `subscribe()` (MessageRouter) and `subscribe_event()` (EventBus) exist on ExtensionContext. If `subscribe()` is missing, add it as part of Phase 1 wiring.

## Consequences

### Comparison with 003-memory


| Aspect              | 003-memory                             | 005-memory                                                                                                     |
| ------------------- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Session memory      | Part of 5-layer model                  | Delegated to [OpenAI SDK Sessions](https://openai.github.io/openai-agents-python/sessions/sqlalchemy_session/) |
| Tables              | 6 (nodes, edges, entities, FTS, vec×2) | 3 (memories, entities, FTS/vec virtual)                                                                        |
| Memory layers       | 5                                      | 1 (kind field) for long-term only                                                                              |
| LLM in hot path     | Yes (slow path per event)              | No                                                                                                             |
| Agent tools         | 13                                     | 5 core + 2 optional                                                                                            |
| Graph edges         | 6 types, separate table                | source_ids + entity_ids JSON                                                                                   |
| Reflections         | Separate table                         | kind='reflection' in memories                                                                                  |
| Implementation size | ~20 Python files                       | ~6–7 files                                                                                                     |


### Benefits

- **Clear session vs long-term split** — SDK Sessions handle in-conversation context; Memory extension handles cross-session knowledge. No overlap, no confusion.
- **Lower complexity** — fewer tables, fewer tools, no LLM in the hot path.
- **Faster ingestion** — hot path <50ms; no blocking on embeddings or extraction.
- **Clear separation of concerns** — Memory stores, retrieves, and owns consolidation logic; self_reflection only triggers it on schedule.
- **Evolutionary implementation** — start with Phase 1, add capabilities incrementally.

### Risks and Mitigations


| Risk                                          | Mitigation                                                                                                                                                                                             |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Regex entity extraction misses some entities  | Phase 4 adds LLM resolution for low-confidence cases.                                                                                                                                                  |
| self_reflection extension not yet implemented | Memory exposes `consolidate()`; self_reflection calls it when available. Until then, `memory_consolidate()` can be an agent tool for manual trigger, or a minimal internal timer (decay + prune only). |
| Context injection requires kernel change      | One generic extension point in loader/router (§10). Documented and minimal.                                                                                                                            |


## Relation to Other ADRs

- **ADR 002** — Memory implements `ServiceProvider` + `ToolProvider`; no `SchedulerProvider`. Memory owns `consolidate()`; self_reflection triggers it on schedule.
- **ADR 003** — Orchestrator uses SDK Sessions for session memory; Memory extension enriches context via long-term retrieval.
- **ADR 004** — Memory subscribes to EventBus `user.message` and MessageRouter `agent_response`. Uses existing pub/sub; no new EventBus topics.
- **assistant3 003-memory** — Superseded by this simplified design for assistant4.

## References

- [OpenAI Agents SDK — SQLAlchemy Sessions](https://openai.github.io/openai-agents-python/sessions/sqlalchemy_session/) — session memory (in-conversation context)
- [event_bus.md](../event_bus.md) — EventBus topics and API
- [extensions.md](../extensions.md) — Extension protocols and context
- Minimalist SOTA / Incremental Knowledge Graph — schema-first, JSON for graph links, LLM off hot path

