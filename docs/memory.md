# Memory System

Long-term hierarchical knowledge graph for the assistant. Persists knowledge across sessions as a three-tier structure: episodes (non-lossy archive), entities + facts (structured triples), and communities (clustered summaries). Surfaces relevant context before each agent response via intent-aware hybrid retrieval. Self-organises through nightly consolidation, decay, and enrichment.

> **Relation to ADR 016.** The system was designed in [ADR 016](adr/016-memory-v3.md), which supersedes [ADR 008](adr/008-memory-v2.md). Key changes: three-tier hierarchy (episodes → entities/facts → communities), atomic fact extraction (ATOM pattern), facts as structured edges between entities, deterministic merge, FTS5 on facts only, and community layer for high-level retrieval.

---

## Memory layers

| Layer | Responsibility | Implementation |
|-------|----------------|----------------|
| **Session memory** | Conversation context within a single discussion | OpenAI Agents SDK `SQLiteSession` — passed to `Runner.run` |
| **Long-term memory** | Cross-session facts, episodes, entity profiles, communities | `memory` extension — SQLite graph database |

Session memory is out of scope for this document. The two layers complement each other: session memory provides working context for the current conversation; long-term memory provides durable knowledge that survives restarts.

---

## Extension

### `memory`

**Roles:** `ToolProvider` + `ContextProvider` + `SchedulerProvider`

A single extension that owns the graph database, all read/write operations, context injection, consolidation, decay, and entity management. Located at `sandbox/extensions/memory/`.

**Files:**

| File | Purpose |
|------|---------|
| `main.py` | `MemoryExtension` — lifecycle, event subscriptions, `ContextProvider`, `SchedulerProvider`, dual-source context |
| `schema.sql` | Full v3 schema: `episodes`, `entities`, `facts`, `communities`, FTS5 on facts, vec tables |
| `storage.py` | `MemoryStorage` — async writer queue, CRUD, graph traversal, BFS expansion |
| `retrieval.py` | `HierarchicalRetriever` — intent classification, hybrid search (FTS5 + vector on facts, BFS from entities, community search), RRF fusion, context assembly |
| `pipeline.py` | `AtomicWritePipeline` — decompose → extract → merge (deterministic). Uses `Agent` with `output_type` for structured LLM output |
| `community.py` | `CommunityManager` — incremental label propagation, community summary generation |
| `decay.py` | `DecayService` — Ebbinghaus decay on facts via `fact_access_log` |
| `tools.py` | Orchestrator tools: `search_memory`, `remember_fact`, `correct_fact`, `confirm_fact`, `get_entity_info`, `memory_stats`, `get_timeline`, `forget_fact` |
| `prompts/` | `decompose.jinja2`, `extract.jinja2`, `community.jinja2` — LLM prompts for pipeline and community summary |

**Architecture:** `MemoryStorage` handles all database operations via an async writer queue (single-writer, WAL mode). `HierarchicalRetriever` provides intent-aware three-tier search (facts + BFS expansion + communities). `AtomicWritePipeline` processes sessions post-completion: atomic decomposition, entity/fact extraction, deterministic entity resolution and fact merge. `CommunityManager` assigns entities to communities and generates LLM summaries. No migration from v2 — memory is populated from scratch.

**Initialization flow:**

1. Opens `{data_dir}/memory.db` and deploys the v3 schema.
2. Starts the async writer task (single-writer queue).
3. Checks the `embedding` extension; if healthy, enables vector search and `EmbeddingIntentClassifier`.
4. Falls back to `KeywordIntentClassifier` if embedding is unavailable.
5. Creates `HierarchicalRetriever` with RRF fusion weights from config.
6. Creates `AtomicWritePipeline` and `CommunityManager` if `ModelRouter` is available.
7. Creates `DecayService` with configured `decay_lambda` and `confidence_threshold`.
8. Subscribes to `user_message` and `agent_response` on the MessageRouter.
9. Subscribes to `session.completed` on the EventBus.

**Context injection (`ContextProvider`):**

Before every agent invocation the kernel calls `get_context(prompt)`. The extension uses a dual-source approach:

1. **Long-term memory (70%)** — `search()` with `return_embedding=True`:
   - Classifies query complexity (simple → budget 600 tokens, complex → 3000).
   - Generates query embedding and runs intent-aware search (FTS5 + vector on facts, BFS expansion from entities, RRF fusion).
   - Calls `search_communities()` with the same embedding (no re-embed) for Tier 3.
   - Assembles context: 50% facts, 25% entity profiles, 25% community summaries.
2. **Current-session episodes (30%)** — raw episodes from the active session (not yet in the graph).
3. Returns a markdown block or `None` if no matches.

**Session change detection:**

When a `user_message` arrives with a new `session_id`, the extension registers the session and triggers consolidation of the old session via `AtomicWritePipeline`. Consolidation runs asynchronously so the hot path stays fast.

**Nightly maintenance (`SchedulerProvider`, daily 03:00) — `run_nightly_maintenance`:**

1. **Consolidate** pending sessions via `AtomicWritePipeline` (decompose → extract → merge).
2. **Retry failed** pipeline queue items (pending/failed with `attempts < pipeline_max_attempts`).
3. **Decay** — apply Ebbinghaus decay to facts; expire facts below `confidence_threshold`.
4. **Enrich** — generate LLM summaries for entities with sparse profiles (min 3 mentions).
5. Compact `fact_access_log` (dedup: keep only most recent access per fact).

**Weekly — `run_community_refresh` (Sunday 04:00):**

Full label propagation on the entity-fact graph; regenerate community assignments and summaries.

**Dependency:** `embedding` (for vector search and embeddings).

---

## Database

All long-term memory is stored in a single SQLite file: `{data_dir}/memory.db`. The database uses WAL journal mode and loads the `sqlite-vec` extension for vector search.

### Three-tier hierarchy

```
┌─────────────────────────────────────────────────┐
│  TIER 3: Communities                            │
│  Clusters of related entities + LLM summaries   │
├─────────────────────────────────────────────────┤
│  TIER 2: Entities + Facts                       │
│  Entities (nodes) + Facts (structured edges)     │
├─────────────────────────────────────────────────┤
│  TIER 1: Episodes                               │
│  Raw messages, immutable, never deleted          │
└─────────────────────────────────────────────────┘
```

---

### Tier 1: `episodes`

Raw conversation turns. Write-once, never modified or deleted. **Not** indexed in FTS5.

```sql
CREATE TABLE episodes (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    actor       TEXT NOT NULL,        -- 'user' | 'assistant' | extension_id
    session_id  TEXT NOT NULL,
    t_obs       INTEGER NOT NULL,    -- observation timestamp (unix ms)
    created_at  INTEGER NOT NULL     -- ingestion timestamp
);
```

---

### Tier 2a: `entities`

Canonical identity anchors. Open `entity_type` taxonomy (no CHECK constraint).

```sql
CREATE TABLE entities (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    aliases       TEXT DEFAULT '[]',  -- JSON array
    summary       TEXT,
    entity_type   TEXT,
    embedding     BLOB,
    mention_count INTEGER DEFAULT 1,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
```

---

### Tier 2b: `facts`

Structured triples: subject → predicate → object. Facts are first-class edges between entities.

```sql
CREATE TABLE facts (
    id                TEXT PRIMARY KEY,
    subject_id        TEXT NOT NULL REFERENCES entities(id),
    predicate         TEXT NOT NULL,   -- ALL_CAPS: WORKS_AT, LIVES_IN, etc.
    object_id         TEXT NOT NULL REFERENCES entities(id),
    fact_text         TEXT NOT NULL,   -- human-readable detail
    embedding         BLOB,
    t_valid           INTEGER,        -- when fact became true
    t_invalid         INTEGER,        -- when fact stopped being true
    t_created        INTEGER NOT NULL,
    t_expired         INTEGER,         -- when superseded in system
    source_episode_id TEXT REFERENCES episodes(id),
    confidence        REAL DEFAULT 1.0,
    invalidated_by    TEXT REFERENCES facts(id)
);
```

**Soft deletion:** `t_expired = now` marks a fact as inactive. `invalidated_by` chains supersession. All active queries use `WHERE t_expired IS NULL`.

**Decay:** Applied to `facts.confidence`. `fact_access_log` records retrieval hits; decay uses `MAX(accessed_at)` per fact. Formula: `confidence × exp(−λ × days_since_access^0.8)`.

**Protected facts:** `confirm_fact` sets `confidence = 1.0` — no decay.

---

### Tier 3: `communities`

Dynamic clusters of related entities with LLM-generated summaries.

```sql
CREATE TABLE communities (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    summary       TEXT NOT NULL,
    embedding     BLOB,
    member_count  INTEGER DEFAULT 0,
    updated_at    INTEGER NOT NULL
);
CREATE TABLE community_members (
    community_id  TEXT REFERENCES communities(id),
    entity_id     TEXT REFERENCES entities(id),
    PRIMARY KEY (community_id, entity_id)
);
```

---

### Supporting tables

| Table | Purpose |
|-------|---------|
| `episode_entities` | Links episodes to entities mentioned in them |
| `fact_access_log` | Insert-only access log for decay (compact: keep latest per fact) |
| `pipeline_queue` | Intermediate state for atomic pipeline retries |
| `maintenance_metadata` | `last_consolidation`, `last_decay_run` timestamps |
| `sessions_consolidations` | Session lifecycle and consolidation status |

---

### Virtual tables

| Table | Purpose |
|-------|---------|
| `facts_fts` | FTS5 full-text search on `facts.fact_text` only |
| `vec_entities` | sqlite-vec KNN for entity embeddings |
| `vec_facts` | sqlite-vec KNN for fact embeddings |
| `vec_communities` | sqlite-vec KNN for community embeddings |

---

## Search

The `memory` extension implements three-tier hierarchical search, combined via Reciprocal Rank Fusion (RRF):

| Strategy | When used | How |
|----------|-----------|-----|
| **FTS5** | Always | `facts_fts MATCH ?` on fact text. |
| **Vector (KNN)** | When `embedding` extension is available | `vec_facts` KNN via sqlite-vec. |
| **BFS graph** | From entities found by vector search | Recursive CTE along fact-edges (subject/object). |
| **Communities** | Via `search_communities()` | `vec_communities` KNN; summaries in context assembly. |

**Intent classification:** Query is classified into `why`, `when`, `who`, `what`, or `general`. `EmbeddingIntentClassifier` (cosine similarity to exemplars, EN + RU) or `KeywordIntentClassifier` (regex fallback).

**Intent-based adjustments:** `who`/`what` get deeper BFS; `when` uses temporal filters (`event_after`, `event_before`).

**RRF merge:** `score = Σ weight_i / (k + rank_i)` across FTS5, vector, and graph (BFS) result lists. Weights and `rrf_k` configurable in manifest.

**Context assembly:** 50% facts (structured triples + fact_text), 25% entity profiles (name + summary), 25% community summaries. `search()` can return embedding via `return_embedding=True` so callers (e.g. `get_context`) avoid re-embedding for community search.

**Time-based filtering:** `search_memory` and `get_timeline` accept `after` and `before`. Formats: `last_week`, `last_month`, `YYYY-MM-DD`.

**Query complexity:** Simple (short, few conjunctions including `и|или|но`) → budget 600, limit 3. Complex → budget 3000, limit 20.

---

## Agent tools

### Orchestrator tools (exposed to the main agent)

| Tool | Description |
|------|-------------|
| `search_memory` | Intent-aware hierarchical search. Supports `entity_name`, `after`, `before` filters. |
| `remember_fact` | Save a structured fact via atomic extraction. Creates entities and fact edge. |
| `correct_fact` | Expire old fact (`t_expired`), create replacement with `invalidated_by` chain. |
| `confirm_fact` | Set `confidence = 1.0` — protected from decay. |
| `get_entity_info` | Entity profile: summary, active facts, community membership. |
| `memory_stats` | Graph metrics: episodes, facts, entities, communities, pending queue items, storage size, maintenance timestamps. |
| `get_timeline` | Chronological facts by `t_valid`. Optional entity and time filters. |
| `forget_fact` | Expire fact by search match. |

---

## Data flows

### Hot path — message ingestion (<5 ms)

```
user_message / agent_response event
  → build episode dict (id, content, actor, session_id, t_obs, created_at)
  → submit to write queue (insert into episodes)
```

No LLM calls. No embedding. Episodes are immutable.

### Session consolidation (triggered on session switch or `session.completed`)

```
_consolidate_session(session_id)
  → is_session_consolidated? → skip if true
  → AtomicWritePipeline.process_session(session_id):
      get_session_episodes
      for each episode:
        decompose → atomic fact strings (LLM, output_type=DecompositionResult)
        enqueue atomic facts (pipeline_queue)
        for each atomic fact (parallel):
          extract → subject, predicate, object (LLM, output_type=ExtractionResult)
        merge_and_persist: entity resolution, fact dedup, temporal conflict (deterministic)
      mark_session_consolidated
```

### Nightly maintenance

```
execute_task("run_nightly_maintenance")
  1. Consolidate pending sessions
  2. Retry failed pipeline queue items
  3. DecayService.apply() → decay facts, expire below threshold, compact fact_access_log
  4. Enrich entities (LLM summaries for entities with ≥3 mentions, sparse summary)
```

### Weekly community refresh

```
execute_task("run_community_refresh")
  → CommunityManager.periodic_refresh() — full label propagation
```

### Ebbinghaus decay

```
DecayService.apply(storage)
  → storage.apply_fact_decay(λ, threshold)
      UPDATE facts SET confidence = confidence * exp(−λ * days_since_access^0.8)
      WHERE t_expired IS NULL AND confidence < 1.0
  → UPDATE facts SET t_expired = now WHERE confidence < threshold
  → compact_fact_access_log (delete old + dedup to latest per fact_id)
```

---

## Configuration

All configuration is in `sandbox/extensions/memory/manifest.yaml`:

| Key | Default | Description |
|-----|---------|-------------|
| `embedding_dimensions` | 256 | Vector embedding dimensions |
| `entity_resolution_threshold` | 0.85 | High-confidence cosine match for entity resolution |
| `entity_resolution_ambiguous_threshold` | 0.70 | Lower bound for LLM disambiguation |
| `fact_dedup_threshold` | 0.90 | Cosine similarity threshold for fact dedup |
| `confidence_threshold` | 0.05 | Prune facts below this confidence |
| `decay_lambda` | 0.1 | λ in decay formula |
| `context_token_budget` | 2000 | Default token budget for context assembly |
| `rrf_k` | 60 | RRF constant |
| `rrf_weight_fts` | 1.0 | FTS5 weight in RRF |
| `rrf_weight_vector` | 1.0 | Vector weight in RRF |
| `rrf_weight_graph` | 1.0 | BFS graph weight in RRF |
| `intent_similarity_threshold` | 0.45 | Min cosine for embedding intent classifier |
| `bfs_max_depth` | 2 | Max BFS expansion depth |
| `bfs_max_facts` | 50 | Max facts from BFS expansion |
| `community_min_shared_facts` | 2 | Min shared facts for community neighbor |
| `community_refresh_interval_days` | 7 | Community refresh schedule |
| `preferred_predicates` | […] | Canonical predicate list for extraction |
| `pipeline_max_attempts` | 3 | Max retries for failed pipeline items |

---

## Embedding integration

The `embedding` extension provides vector embeddings used by memory for semantic search.

The memory extension calls embedding at:

- **Context injection** — query embedding for hybrid search and community search (single embed, reused).
- **`remember_fact`** — fact text embedding for new fact.
- **Consolidation** — `embed_batch()` for entity names; single embed per fact for dedup and save.
- **Entity enrichment** — not used; summaries are text-only.
- **Community summaries** — embedding for `vec_communities`.

If the `embedding` extension is unavailable, memory falls back to FTS5-only search with `KeywordIntentClassifier`.

---

## Manual maintenance

To trigger nightly maintenance manually:

```bash
uv run python scripts/run_memory_maintenance.py
```

Use `--dry-run` to show stats only without running the pipeline.
