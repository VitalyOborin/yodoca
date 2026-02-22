# Memory System

Long-term memory for the assistant. Persists knowledge across sessions, surfaces relevant context before each agent response, and organises itself through nightly maintenance.

> **Relation to ADR 005.** The system was designed in [ADR 005](adr/005-memory.md). During implementation several improvements were made: the `ContextProvider` protocol was adopted instead of the kernel-level injection proposed in §10; the `memory_entities` junction table was added from day one (not deferred to Phase 2); a `sessions_consolidations` table was introduced for robust session lifecycle tracking; the NER extension was implemented as a fully configurable multi-provider pipeline; and two-level fact deduplication was added to the consolidation path. These deviations consistently improved the design. ADR 005 is partially outdated; this document reflects the actual implementation.

---

## Memory layers

| Layer | Responsibility | Implementation |
|---|---|---|
| **Session memory** | Conversation context within a single discussion | OpenAI Agents SDK `SQLAlchemySession` — passed to `Runner.run` |
| **Long-term memory** | Cross-session facts, episodes, preferences, reflections | `memory` extension — SQLite database |

Session memory is out of scope for this document. The two layers complement each other: session memory provides working context for the current conversation; long-term memory provides durable knowledge that survives restarts.

---

## Extensions

### `memory`

**Roles:** `ToolProvider` + `ContextProvider`

The core extension. Owns the database, all read/write operations, context injection, and consolidation API. Located at `sandbox/extensions/memory/`.

**Files:**

| File | Purpose |
|---|---|
| `main.py` | `MemoryExtension` — lifecycle, event subscriptions, `ContextProvider`, public API |
| `db.py` | `MemoryDatabase` — SQLite connection, schema deployment, `sqlite-vec` integration |
| `repository.py` | `MemoryRepository` — all CRUD, FTS5/vector/hybrid search, decay, session management |
| `tools.py` | Agent tools for the Orchestrator and for the consolidator agent (two separate sets) |
| `entity_linker.py` | Bridges the NER extension output to `MemoryRepository` entity operations |

**Initialization flow:**

1. Opens `{data_dir}/memory.db` and deploys the schema.
2. Checks the `embedding` extension; if healthy and dimensions match, enables vector search (`_embed_fn`).
3. Checks the `ner` extension; if healthy, enables entity extraction (`_entity_link_fn`).
4. Subscribes to `user_message` and `agent_response` on the MessageRouter.

**Context injection (`ContextProvider`):**

Before every agent invocation the kernel calls `get_context(prompt)`. The extension:

1. Generates a query embedding (if the `embedding` extension is available).
2. Runs a hybrid search over `fact` memories, excluding the current session.
3. Appends the latest `reflection` (from cache; invalidated when a new reflection is saved).
4. Returns a markdown block prepended to the system prompt.

**Session consolidation trigger:**

When a `user_message` arrives with a new `session_id`, the extension registers the session in `sessions_consolidations` and emits `memory.session_completed` for all previously open, unconsolidated sessions. This ensures that completed sessions are consolidated promptly — without waiting for the nightly schedule — the moment the user starts a new conversation.

**Entity enrichment API (for memory_maintenance):**

| Method / property | Description |
|---|---|
| `accurate_ner_available` | `True` if spaCy or LLM NER providers are loaded. Enrichment is skipped when `False`. |
| `get_memories_for_entity_enrichment(kinds, max_entity_count, limit)` | Fetch memories with `enriched_at` unset and entity count ≤ threshold. Used to select candidates for re-extraction. |
| `enrich_memory_entities(memory_id, content)` | Re-extract entities with `strategy="accurate"`, link them, and stamp `attributes.enriched_at`. Returns count of entity links. |

---

### `memory_maintenance`

**Roles:** `AgentProvider` + `SchedulerProvider`

Owns no data. Drives three scheduled maintenance tasks by calling back into the `memory` extension. Located at `sandbox/extensions/memory_maintenance/`.

**Scheduled tasks:**

| Cron | Task | What happens |
|---|---|---|
| `0 2 * * *` | `execute_entity_enrichment` | Re-extracts entities with accurate NER (spaCy/LLM) for memories with few entities |
| `0 3 * * *` | `execute_consolidation` | Fetches all pending sessions from `memory`, emits `memory.session_completed` for each |
| `0 4 * * *` | `execute_decay` | Calls `memory.run_decay_and_prune(threshold)` — Ebbinghaus decay on all active facts |

**Consolidator agent:**

An `Agent` built at initialization with the consolidator tools from `memory.get_consolidator_tools()`. Invoked when a `memory.session_completed` event is received. Follows this loop:

1. `is_session_consolidated` — idempotency guard.
2. `get_episodes_for_consolidation` (paginated, 30 episodes per chunk) — reads raw dialogue.
3. `save_facts_batch` — LLM extracts semantic facts; two-level deduplication is applied at save time.
4. For each saved fact: `detect_conflicts` → `resolve_conflict` (if needed).
5. `mark_session_consolidated` — records completion.

---

### `memory_reflection`

**Roles:** `SchedulerProvider`

Weekly meta-cognitive reflection. Owns no data. Fetches recent facts and episodes from `memory`, runs an internal LLM agent, saves a `reflection` memory. Located at `sandbox/extensions/memory_reflection/`.

**Scheduled tasks:**

| Cron | Task | What happens |
|---|---|---|
| `0 3 * * 0` | `execute_reflection` | Fetches recent facts and episodes, runs reflection LLM agent, saves `reflection` memory |

Runs at most once per 6 days (idempotency check on last reflection timestamp). Requires at least 3 facts in the past 7 days to proceed. Produces a `reflection` memory tagged `weekly`.

---

### `embedding`

Provides vector embeddings used by the `memory` extension for semantic search. Uses `text-embedding-3-large` with 256-dimensional Matryoshka reduction — approximately 95% of full-model quality at 1/12th the storage cost.

The `memory` extension calls `embedding_ext.embed(text, dimensions=256)` at two points:

- During context injection (`get_context`) — to generate a query embedding for vector search.
- After saving a fact or episode — to store the embedding in `vec_memories`.

If the `embedding` extension is unavailable or dimensions mismatch, `memory` falls back to FTS5 + entity search only (no vector component).

---

### `ner`

Named Entity Recognition. Extracts named entities from text with a configurable multi-provider pipeline.

**Providers (composable per strategy):**

| Provider | Description |
|---|---|
| `regex` | Regex patterns for mentions (`@name`), hashtags (`#tag`), emails, URLs, dates. Always available; no external dependencies. |
| `spacy` | spaCy NLP pipeline (optional; requires model installation). |
| `llm` | LLM-based extraction (optional; highest quality, highest latency). |

**Strategies** define which providers run and in what order. The `fast` strategy uses the `regex` provider only (enabled by default). Results from multiple providers are deduplicated by span overlap — the highest-confidence, longest-span entity wins.

The `memory` extension calls `ner_ext.extract(text, strategy="fast")` after saving each episode or fact. The results are mapped by `entity_linker.py` and stored in the `entities` table with a link in `memory_entities`.

The nightly `execute_entity_enrichment` task (02:00) re-processes memories with few entities using `strategy="accurate"` (regex + spaCy + LLM). This extracts person names, organizations, projects, and locations that regex alone misses. Enrichment is skipped when no spaCy or LLM provider is available.

---

## Database

All long-term memory is stored in a single SQLite file: `{data_dir}/memory.db`. The database uses WAL journal mode and loads the `sqlite-vec` extension for vector search.

### `memories`

The central table. All memory kinds live here; the `kind` column differentiates them.

```sql
CREATE TABLE memories (
    id            TEXT PRIMARY KEY,      -- ep_*, fact_*, ref_* prefix by kind
    kind          TEXT NOT NULL,         -- episode | fact | reflection
    content       TEXT NOT NULL,
    session_id    TEXT,                  -- source session (for episodes and extracted facts)
    embedding     BLOB,                  -- reserved; embeddings live in vec_memories

    event_time    INTEGER NOT NULL,      -- when the event occurred (Unix epoch)
    created_at    INTEGER NOT NULL,      -- when recorded in DB (Unix epoch)
    valid_until   INTEGER,               -- NULL = active; non-NULL = soft-deleted

    confidence    REAL DEFAULT 1.0,      -- 0.0–1.0; decays over time for facts
    access_count  INTEGER DEFAULT 0,
    last_accessed INTEGER,
    decay_rate    REAL DEFAULT 0.1,      -- 0.0 = protected (no decay)

    source_ids    TEXT DEFAULT '[]',     -- JSON: [memory_id, ...] — provenance
    source_role   VARCHAR(255),          -- 'user' or agent name (for episodes)
    entity_ids    TEXT DEFAULT '[]',     -- JSON: [entity_id, ...] — denormalized
    tags          TEXT DEFAULT '[]',     -- JSON: ["work", "project_alpha"]
    attributes    TEXT DEFAULT '{}'      -- extensible metadata JSON (enriched_at for entity enrichment)
);
```

**Memory kinds:**

| Kind | Saved by | Description |
|---|---|---|
| `episode` | `memory` extension, on every `user_message` and `agent_response` | Raw dialogue turn. Full conversation history. |
| `fact` | Consolidator agent (`save_facts_batch`) or Orchestrator (`remember_fact` tool) | Semantic fact extracted from dialogue or explicitly remembered. Subject to Ebbinghaus decay. |
| `reflection` | `memory_reflection` weekly task | Meta-cognitive weekly summary. `decay_rate = 0.0`; never decays. |

**Soft deletion:** records are never physically removed. `valid_until = now()` marks a record as inactive. All queries include `WHERE valid_until IS NULL`.

**Decay:** `confidence_new = confidence × exp(−decay_rate × days_since_access)`. Facts with `confidence < 0.05` (configurable) are soft-deleted during the nightly `execute_decay` task.

**Protected facts:** `decay_rate = 0.0` means confidence never changes. Set by `confirm_fact` tool or automatically on `reflection` records.

---

### `entities`

Named entity registry. Prevents the same real-world entity from being stored under multiple names.

```sql
CREATE TABLE entities (
    id             TEXT PRIMARY KEY,       -- ent_*
    canonical_name TEXT NOT NULL,
    type           TEXT NOT NULL,          -- person | project | organization | location | email | url | other
    aliases        TEXT DEFAULT '[]',      -- JSON: alternative names / raw mentions
    summary        TEXT,
    embedding      BLOB,
    mention_count  INTEGER DEFAULT 1,
    protected      INTEGER DEFAULT 0       -- 1 = no decay applied (reserved)
);
```

On each NER extraction, `MemoryRepository.create_or_get_entity` looks up by `canonical_name` + `type`, then by alias match. If found, increments `mention_count` and merges new aliases. If not found, creates a new record.

---

### `memory_entities`

Junction table linking memories to entities. Enables efficient entity-based queries without full-table scans on JSON columns.

```sql
CREATE TABLE memory_entities (
    memory_id TEXT NOT NULL REFERENCES memories(id),
    entity_id TEXT NOT NULL REFERENCES entities(id),
    PRIMARY KEY (memory_id, entity_id)
);
CREATE INDEX idx_me_entity ON memory_entities(entity_id);
CREATE INDEX idx_me_memory ON memory_entities(memory_id);
```

Added from day one (ADR planned it as a Phase 2 addition). All entity-linked searches use this table via `INNER JOIN`.

---

### `memories_fts`

FTS5 virtual table for full-text search. Automatically kept in sync by database triggers on `INSERT` and `UPDATE OF content` on `memories`.

```sql
CREATE VIRTUAL TABLE memories_fts USING fts5(
    content,
    content='memories',
    content_rowid=rowid,
    tokenize='unicode61'
);
```

---

### `vec_memories`

Vector search virtual table powered by `sqlite-vec`. Stores 256-dimensional float32 embeddings keyed by `memory_id`.

```sql
CREATE VIRTUAL TABLE vec_memories USING vec0(
    memory_id TEXT PRIMARY KEY,
    embedding float[256]
);
```

Created on first initialization and locked to the configured dimensions. If a dimension mismatch is detected on startup (e.g., after changing the embedding model), vector search is disabled and a warning is logged with instructions for manual reset.

---

### `sessions_consolidations`

Tracks session lifecycle for consolidation. Registered when a `user_message` with a new `session_id` is first seen.

```sql
CREATE TABLE sessions_consolidations (
    session_id       TEXT PRIMARY KEY,
    first_seen_at    INTEGER NOT NULL,
    consolidated_at  INTEGER           -- NULL = pending; non-NULL = done
);
```

`get_pending_consolidations(exclude_session_id)` returns sessions where `consolidated_at IS NULL` and `session_id != current`. Used both by the `memory` extension (trigger on session switch) and by `memory_maintenance` (nightly schedule).

---

### `memory_metadata`

Key-value store for database-level configuration, used to detect schema incompatibilities across restarts.

```sql
CREATE TABLE memory_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Currently stores `embedding_dimensions` — the dimension count used when `vec_memories` was created.

---

## Search

The `memory` extension implements three search strategies, combined via Reciprocal Rank Fusion (RRF):

| Strategy | When used | How |
|---|---|---|
| **FTS5** | Always | `memories_fts MATCH ?` ranked by BM25. |
| **Vector (KNN)** | When `embedding` extension is available | `vec_memories` KNN via `sqlite-vec`. |
| **Entity** | Always | `INNER JOIN memory_entities → entities` matched by `canonical_name` / `aliases`. |

**RRF merge:** `score(memory) = Σ 1 / (60 + rank_i)` across all lists in which the memory appears. Top-N by fused score are returned. This consistently outperforms any single method.

Context injection calls `hybrid_search(prompt, kind="fact", limit=5, exclude_session_id=current)` — only facts from previous sessions are injected.

---

## Agent tools

### Orchestrator tools (exposed to the main agent)

| Tool | Description |
|---|---|
| `search_memory` | Hybrid search. Main retrieval tool. |
| `remember_fact` | Explicitly save a fact. Generates embedding and links entities. |
| `correct_fact` | Soft-delete old fact, create replacement. |
| `confirm_fact` | Set `decay_rate=0.0, confidence=1.0` — permanently protected. |
| `memory_stats` | Count by kind, latest record timestamp. |
| `get_entity_info` | Entity profile + linked memories. For "tell me everything about X". |

### Consolidator tools (internal — not exposed to Orchestrator)

| Tool | Description |
|---|---|
| `get_episodes_for_consolidation` | Fetch session episodes paginated (30 per chunk). |
| `save_facts_batch` | Save multiple facts with two-level deduplication (intra-batch exact + Jaccard ≥ 0.75 against DB). |
| `mark_session_consolidated` | Record consolidation completion. |
| `is_session_consolidated` | Idempotency check before processing. |
| `detect_conflicts` | Hybrid search for facts that may contradict the new fact. |
| `resolve_conflict` | Lower old fact confidence to 0.3, attach `supersedes` attribute to new fact. |

---

## Data flows

### Hot path — message ingestion (synchronous, <50 ms)

```
user_message / agent_response event
  → save_episode(content, session_id, source_role)
      → INSERT INTO memories (kind='episode')
      → FTS5 trigger fires automatically
  → extract_and_link(memory_id, content)   [if ner available]
      → ner_ext.extract(text, strategy='fast')
      → create_or_get_entity + link_memory_to_entities
```

No LLM calls. No embeddings. No blocking.

### Context injection (before each agent invocation)

```
ContextProvider.get_context(prompt)
  → embed_fn(prompt)                      [if embedding available]
  → hybrid_search(prompt, kind='fact', limit=5, exclude_current_session)
  → get_latest_reflection()               [from cache]
  → return "## Relevant memory\n..." + "## Weekly insight\n..."
```

### Session consolidation (triggered on session switch + nightly at 03:00)

```
new session_id detected in user_message
  → ensure_session(session_id)
  → get_pending_consolidations(exclude=current)
  → emit("memory.session_completed", {session_id})

memory.session_completed event received by memory_maintenance
  → invoke consolidator agent:
      is_session_consolidated?  → skip if true
      get_episodes_for_consolidation (paginated)
      [LLM] extract facts from dialogue
      save_facts_batch → dedup → INSERT facts
      for each fact: detect_conflicts → resolve_conflict if needed
      mark_session_consolidated
```

### Nightly decay (daily at 04:00)

```
memory_maintenance.execute_decay
  → memory.run_decay_and_prune(threshold=0.05)
      → get_facts_for_decay()
      → for each fact:
            new_conf = conf × exp(−decay_rate × days_since_access)
            if new_conf < threshold: soft_delete + remove from vec_memories + memory_entities
            else: UPDATE confidence
```

### Entity enrichment (daily at 02:00)

```
memory_maintenance.execute_entity_enrichment
  → memory.accurate_ner_available?      → skip if False
  → memory.get_memories_for_entity_enrichment(kinds=['fact','episode'], max_entity_count=2, limit=50)
  → for each memory:
        memory.enrich_memory_entities(memory_id, content)
            → ner_ext.extract(content, strategy='accurate')
            → create_or_get_entity + link_memory_to_entities
            → mark_memory_enriched (attributes.enriched_at = now)
```

Runs before consolidation so newly linked entities are visible during fact extraction.

### Weekly reflection (every Sunday at 03:00)

```
memory_reflection.execute_reflection
  → get_latest_reflection_timestamp()    → skip if < 6 days ago
  → get_recent_memories_for_reflection(days=7, limit=200)
  → skip if facts < 3
  → [LLM] reflection agent.run(prompt with recent facts+episodes)
  → memory.save_reflection(content, source_ids, tags=['weekly'])
      → INSERT INTO memories (kind='reflection', decay_rate=0.0)
      → generate and store embedding
      → invalidate _reflection_cache
```
