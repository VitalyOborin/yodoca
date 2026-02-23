# Memory System

Long-term graph-based cognitive memory for the assistant. Persists knowledge across sessions as a typed graph of nodes, edges, and entities. Surfaces relevant context before each agent response via intent-aware hybrid retrieval. Self-organises through nightly consolidation, decay, and enrichment.

> **Relation to ADR 008.** The system was designed in [ADR 008](adr/008-memory-v2.md), replacing the flat-table memory from [ADR 005](adr/005-memory.md). Key changes: graph schema (nodes + edges + entities), LLM-powered write-path agent (replaces `memory_maintenance`, `memory_reflection`, and `ner` satellite extensions), intent-aware retrieval with graph traversal, and Ebbinghaus decay with access reinforcement.

---

## Memory layers

| Layer | Responsibility | Implementation |
|---|---|---|
| **Session memory** | Conversation context within a single discussion | OpenAI Agents SDK `SQLiteSession` — passed to `Runner.run` |
| **Long-term memory** | Cross-session facts, episodes, procedures, opinions | `memory` extension — SQLite graph database |

Session memory is out of scope for this document. The two layers complement each other: session memory provides working context for the current conversation; long-term memory provides durable knowledge that survives restarts.

---

## Extension

### `memory`

**Roles:** `ToolProvider` + `ContextProvider` + `SchedulerProvider`

A single extension that owns the graph database, all read/write operations, context injection, consolidation, decay, and entity management. Located at `sandbox/extensions/memory/`.

**Files:**

| File | Purpose |
|---|---|
| `main.py` | `MemoryExtension` — lifecycle, event subscriptions, `ContextProvider`, `SchedulerProvider`, nightly pipeline |
| `schema.sql` | Full graph schema: `nodes`, `edges`, `entities`, `node_entities`, FTS5, vec tables |
| `storage.py` | `MemoryStorage` — async writer queue, CRUD, graph traversal, aggregation queries |
| `retrieval.py` | `MemoryRetrieval` — intent classification, hybrid search (FTS5 + vector + graph + RRF), context assembly |
| `tools.py` | Orchestrator tools: `search_memory`, `remember_fact`, `correct_fact`, `confirm_fact`, `get_entity_info`, `memory_stats`, `explain_fact`, `weak_facts` |
| `agent_tools.py` | Write-path agent tools (internal): `save_nodes_batch`, `extract_and_link_entities`, `detect_conflicts`, `resolve_conflict`, `save_causal_edges`, etc. |
| `agent.py` | `MemoryAgent` — LLM-powered consolidation and causal inference agent |
| `prompt.jinja2` | System prompt for consolidation and causal inference modes |
| `decay.py` | `DecayService` — Ebbinghaus decay and pruning |

**Architecture:** `MemoryStorage` handles all database operations via an async writer queue (single-writer, WAL mode). `MemoryRetrieval` provides intent-aware hybrid search. `MemoryAgent` (private, not an `AgentProvider`) handles write-path operations (consolidation, entity enrichment, causal inference). The extension exposes tools and context to the Orchestrator and runs nightly maintenance via `SchedulerProvider`.

**Initialization flow:**

1. Opens `{data_dir}/memory.db` and deploys the schema.
2. Starts the async writer task (single-writer queue).
3. Checks the `embedding` extension; if healthy, enables vector search and the `EmbeddingIntentClassifier`.
4. Falls back to `KeywordIntentClassifier` if embedding is unavailable.
5. Creates `MemoryRetrieval` with RRF fusion weights from config.
6. Creates the write-path `MemoryAgent` if `ModelRouter` is available.
7. Creates `DecayService` with configured threshold.
8. Subscribes to `user_message` and `agent_response` on the MessageRouter.
9. Subscribes to `session.completed` on the EventBus.

**Context injection (`ContextProvider`):**

Before every agent invocation the kernel calls `get_context(prompt)`. The extension:

1. Classifies query complexity (simple → budget 1000 tokens, complex → 3000).
2. Generates a query embedding (if the `embedding` extension is available).
3. Runs intent-aware hybrid search (FTS5 + vector + graph traversal via RRF).
4. Assembles context with budget shares: facts 40%, entity profiles 25%, temporal context 25%, evidence 10%.
5. Returns a markdown block prepended to the system prompt, or `None` if no matches.

**Session change detection:**

When a `user_message` arrives with a new `session_id`, the extension registers the session and triggers consolidation of the old session. This ensures completed sessions are processed promptly — without waiting for the nightly schedule.

**Nightly maintenance (`SchedulerProvider`, daily 03:00):**

1. **Consolidate** pending sessions via the write-path agent.
2. **Decay** — apply Ebbinghaus decay to non-episodic nodes; prune below threshold.
3. **Enrich** — generate LLM summaries for entities with sparse profiles.
4. **Causal inference** — analyze consecutive episode pairs for cause-effect relationships.

**Dependency:** `embedding` (for vector search and embedding generation).

---

## Database

All long-term memory is stored in a single SQLite file: `{data_dir}/memory.db`. The database uses WAL journal mode and loads the `sqlite-vec` extension for vector search.

### `nodes`

The central table. All cognitive types live here; the `type` column differentiates them.

```sql
CREATE TABLE nodes (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL CHECK(type IN ('episodic','semantic','procedural','opinion')),
    content          TEXT NOT NULL,
    embedding        BLOB,

    event_time       INTEGER NOT NULL,    -- when the event actually occurred
    created_at       INTEGER NOT NULL,    -- when recorded in DB
    valid_from       INTEGER NOT NULL,    -- start of fact validity
    valid_until      INTEGER,             -- NULL = still valid (soft-delete sets this)

    confidence       REAL NOT NULL DEFAULT 1.0,
    access_count     INTEGER NOT NULL DEFAULT 0,
    last_accessed    INTEGER,
    decay_rate       REAL NOT NULL DEFAULT 0.1,

    source_type      TEXT,     -- conversation | tool_result | extraction | consolidation
    source_role      TEXT,     -- user | orchestrator | memory_agent
    session_id       TEXT,     -- links episodic nodes to their conversation session
    attributes       TEXT DEFAULT '{}'
);
```

**Node types:**

| Type | Saved by | Description |
|---|---|---|
| `episodic` | Hot path, on every `user_message` and `agent_response` | Raw dialogue turn. Full conversation history. |
| `semantic` | Write-path agent (consolidation) or Orchestrator (`remember_fact` tool) | Durable facts about the user, world, or context. Subject to Ebbinghaus decay. |
| `procedural` | Write-path agent (consolidation) | Action patterns and how-to knowledge. Subject to decay. |
| `opinion` | Write-path agent (consolidation) | Subjective preferences and assessments. Subject to decay. |

**Soft deletion:** Records are never physically removed. `valid_until = now` marks a record as inactive. All queries include `WHERE valid_until IS NULL`.

**Decay:** `confidence_new = confidence × exp(−decay_rate × days_since_access^0.8)`. Non-episodic nodes with `confidence < 0.05` (configurable) are soft-deleted during nightly maintenance.

**Protected facts:** `decay_rate = 0.0` means confidence never changes. Set by `confirm_fact` tool.

**Access reinforcement:** When a node is returned by a search, `access_count` is incremented and `last_accessed` is updated. Confidence receives a small boost: `+0.05 × log(1 + access_count / 20)`, capped at 1.0.

---

### `edges`

Typed relationships between nodes. Enable graph traversal for temporal chains, causal reasoning, provenance tracking, and knowledge evolution.

```sql
CREATE TABLE edges (
    id               TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id        TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,

    relation_type    TEXT NOT NULL CHECK(relation_type IN
        ('temporal','causal','entity','derived_from','supersedes')),
    predicate        TEXT,
    weight           REAL NOT NULL DEFAULT 1.0,
    confidence       REAL NOT NULL DEFAULT 1.0,

    valid_from       INTEGER NOT NULL,
    valid_until      INTEGER,
    evidence         TEXT DEFAULT '[]',
    created_at       INTEGER NOT NULL
);
```

**Edge types:**

| Type | Created by | Purpose |
|---|---|---|
| `temporal` | Hot path (automatic) | Links consecutive episodes within a session. Enables timeline traversal. |
| `causal` | Nightly maintenance (LLM inference) | Cause-effect relationships between episodes. Default `confidence = 0.7`. |
| `entity` | Write-path agent | Links nodes sharing the same entity mention. |
| `derived_from` | Write-path agent | Links extracted facts to their source episodes (provenance). |
| `supersedes` | `correct_fact` tool / write-path agent | Links new fact to the old fact it replaces (knowledge evolution). |

---

### `entities`

Named entity registry. Canonical identities for real-world entities.

```sql
CREATE TABLE entities (
    id               TEXT PRIMARY KEY,
    canonical_name   TEXT NOT NULL,
    type             TEXT NOT NULL CHECK(type IN
        ('person','project','organization','place','concept','tool')),
    aliases          TEXT DEFAULT '[]',   -- JSON: alternative names
    summary          TEXT,                -- LLM-generated entity description
    embedding        BLOB,
    first_seen       INTEGER NOT NULL,
    last_updated     INTEGER NOT NULL,
    mention_count    INTEGER NOT NULL DEFAULT 1,
    attributes       TEXT DEFAULT '{}'
);
```

On each entity extraction (during consolidation), the write-path agent resolves entity mentions by canonical name and alias. If found, increments `mention_count` and merges new aliases. If not found, creates a new record.

---

### `node_entities`

Junction table linking nodes to entities. Enables efficient entity-based queries.

```sql
CREATE TABLE node_entities (
    node_id    TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id, entity_id)
);
```

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

---

### Virtual tables

| Table | Purpose |
|---|---|
| `nodes_fts` | FTS5 full-text search on `nodes.content`. Kept in sync by INSERT/UPDATE/DELETE triggers. |
| `vec_nodes` | sqlite-vec KNN search. 256-dimensional float32 embeddings keyed by `node_id`. |
| `vec_entities` | sqlite-vec KNN search for entity embeddings. |

---

## Search

The `memory` extension implements four search strategies, combined via Reciprocal Rank Fusion (RRF):

| Strategy | When used | How |
|---|---|---|
| **FTS5** | Always | `nodes_fts MATCH ?` ranked by BM25. |
| **Vector (KNN)** | When `embedding` extension is available | `vec_nodes` KNN via `sqlite-vec`. |
| **Graph traversal** | When intent routing activates a graph strategy | Temporal chain, causal chain, or entity-based node lookup. |
| **Entity** | For "who/what" intents when entity is identified | `node_entities` JOIN to find nodes linked to the entity. |

**Intent classification:** Before search, the query is classified into an intent (`why`, `when`, `who`, `what`, `general`). Two classifiers are available:

| Classifier | When used | How |
|---|---|---|
| `EmbeddingIntentClassifier` | When `embedding` extension is available | Cosine similarity against pre-embedded exemplars (EN + RU). Multilingual, <2ms. |
| `KeywordIntentClassifier` | Fallback when embedding is unavailable | Regex-based, English-only, <1ms. |

**Intent-based strategy routing:**

| Intent | Graph strategy | Additional filters |
|---|---|---|
| `why` | Causal chain BFS from seed nodes | — |
| `when` | Temporal chain traversal (forward/backward) | `event_after` / `event_before` |
| `who` / `what` | Entity-based node lookup via `node_entities` | Entity resolution by name/alias |
| `general` | No graph traversal | Hybrid FTS5 + vector only |

**RRF merge:** `score(node) = Σ weight_i / (k + rank_i)` across FTS5, vector, and graph result lists. Weights (`rrf_weight_fts`, `rrf_weight_vector`, `rrf_weight_graph`) and `rrf_k` are configurable in the manifest.

**Time-based filtering:** `search_memory` accepts `after` and `before` parameters. Supported formats: `last_week`, `last_month`, `YYYY-MM-DD`.

**Adaptive complexity:** Queries are classified as `simple` or `complex`. Simple queries use graph depth 2 and limit 5; complex queries use depth 4 and limit 20.

---

## Agent tools

### Orchestrator tools (exposed to the main agent)

| Tool | Description |
|---|---|
| `search_memory` | Intent-aware hybrid search. Supports `type`, `entity_name`, `after`, `before` filters. |
| `remember_fact` | Explicitly save a semantic fact. Generates embedding. |
| `correct_fact` | Soft-delete old fact, create replacement with `supersedes` edge. |
| `confirm_fact` | Set `decay_rate=0.0, confidence=1.0` — permanently protected from decay. |
| `get_entity_info` | Entity profile: summary, related facts, timeline. |
| `memory_stats` | Graph metrics: node/edge counts by type, entities, orphan nodes, storage size, maintenance timestamps. |
| `explain_fact` | Provenance chain: source episodes (`derived_from`), supersedes chain, linked entities. |
| `weak_facts` | List low-confidence facts that may need confirmation or will decay soon. |

### Write-path agent tools (internal — not exposed to Orchestrator)

| Tool | Description |
|---|---|
| `is_session_consolidated` | Idempotency check before processing. |
| `get_session_episodes` | Fetch session episodes paginated. |
| `save_nodes_batch` | Save extracted facts/procedures/opinions with `derived_from` edges and batch embeddings. |
| `extract_and_link_entities` | LLM-powered entity extraction and resolution for a batch of nodes. |
| `detect_conflicts` | Hybrid search for potentially contradicting facts. |
| `resolve_conflict` | Soft-delete old fact, create `supersedes` edge. |
| `mark_session_consolidated` | Record consolidation completion. |
| `save_causal_edges` | Create `causal` edges between episode pairs (confidence 0.7). |
| `update_entity_summary` | Update entity summary and re-embed. |

---

## Data flows

### Hot path — message ingestion (synchronous, <50 ms)

```
user_message / agent_response event
  → generate episodic node (type='episodic', content, session_id, source_role)
  → submit to write queue (fire-and-forget)
  → FTS5 trigger fires automatically on INSERT
  → query last episode in session → create temporal edge (fire-and-forget)
  → if embedding available: asyncio.create_task(slow_path)
```

No LLM calls. No blocking waits.

### Slow path — embedding generation (async, ~200 ms)

```
_slow_path(node_id, content)
  → embed_fn(content)           [via embedding extension]
  → save_embedding(node_id, embedding)  [UPDATE node + INSERT vec_nodes]
```

### Context injection (before each agent invocation)

```
ContextProvider.get_context(prompt)
  → classify_query_complexity(prompt)  → adaptive params (limit, budget, graph_depth)
  → embed_fn(prompt)                   [if embedding available]
  → intent_classifier.classify(prompt)
  → hybrid search: FTS5 + vector + graph strategy (by intent) → RRF fusion
  → assemble_context(results, token_budget)
      → Facts 40% | Entity profiles 25% | Temporal context 25% | Evidence 10%
  → return formatted markdown or None
```

### Session consolidation (triggered on session switch + nightly at 03:00)

```
new session_id detected in user_message
  → ensure_session(session_id)
  → asyncio.create_task(_consolidate_session(old_session_id))

_consolidate_session(session_id)
  → write_agent.consolidate_session(session_id):
      is_session_consolidated?  → skip if true
      get_session_episodes (paginated)
      [LLM] extract semantic facts, procedural patterns, opinions
      save_nodes_batch → INSERT nodes + derived_from edges + batch embed
      extract_and_link_entities → resolve or create entity anchors
      detect_conflicts → resolve_conflict if needed
      mark_session_consolidated
```

### Nightly maintenance (daily at 03:00)

```
execute_task("run_nightly_maintenance")
  1. Consolidate pending sessions (for each unconsolidated session)
  2. Apply Ebbinghaus decay + prune below threshold
  3. Entity enrichment (LLM summaries for entities with ≥3 mentions and no summary)
  4. Causal inference (analyze consecutive episode pairs for cause-effect)
```

### Ebbinghaus decay

```
DecayService.apply(storage)
  → get_decayable_nodes()  [non-episodic, decay_rate > 0, valid_until IS NULL]
  → for each node:
        days_since = (now - last_accessed) / 86400
        new_conf = confidence × exp(−decay_rate × days_since^0.8)
        if new_conf < threshold: mark for pruning (soft-delete)
        else: batch update confidence
```

---

## Configuration

All configuration is in `sandbox/extensions/memory/manifest.yaml`:

| Key | Default | Description |
|---|---|---|
| `embedding_dimensions` | 256 | Vector embedding dimensions |
| `decay_threshold` | 0.05 | Prune nodes below this confidence |
| `decay_rate_default` | 0.1 | Default λ in decay formula |
| `entity_enrichment_min_mentions` | 3 | Min mentions before entity enrichment |
| `causal_inference_batch_size` | 50 | Max episode pairs per nightly causal run |
| `context_token_budget` | 2000 | Default token budget for context assembly |
| `rrf_k` | 60 | RRF constant (higher → flatter rank contribution) |
| `rrf_weight_fts` | 1.0 | FTS5 weight in RRF fusion |
| `rrf_weight_vector` | 1.0 | Vector search weight in RRF fusion |
| `rrf_weight_graph` | 1.0 | Graph traversal weight in RRF fusion |
| `intent_similarity_threshold` | 0.45 | Min cosine similarity for embedding intent classifier |

---

## Embedding integration

The `embedding` extension provides vector embeddings used by memory for semantic search. Uses `text-embedding-3-large` with 256-dimensional Matryoshka reduction.

The memory extension calls embedding at several points:

- **Slow path** — after episodic node creation, `asyncio.create_task` generates and saves the embedding.
- **Context injection** — query embedding for hybrid search.
- **`remember_fact`** / **`correct_fact`** — embedding for newly created semantic nodes.
- **Consolidation** — `embed_batch()` for batch embedding of extracted nodes.
- **Entity enrichment** — re-embed entity summaries.

If the `embedding` extension is unavailable, memory falls back to FTS5-only search with `KeywordIntentClassifier`.
