# ADR 008: Memory v2 — Graph-Based Cognitive Memory with Agent Write-Path

## Status

Accepted. Implemented. **Supersedes ADR 005.**

## Context

ADR 005 delivered a pragmatic memory system: a flat `memories` table with `kind` field, Jaccard deduplication, session-based consolidation, and auxiliary extensions (`memory_maintenance`, `memory_reflection`, `ner`). This design prioritized simplicity over capability and served as a working baseline.

In production use, three structural problems became clear:

1. **Flat model blocks relationship reasoning.** Without `edges`, there is no way to express "fact X supersedes fact Y," "episode A caused episode B," or "entity E is mentioned in episodes A, B, C." Provenance links are stored as `source_ids` JSON — unindexable, unqueryable, no typed relations. Timeline, causal chains, and entity graphs are impossible without fundamental schema changes.
2. **Satellite extensions are workarounds, not solutions.** `memory_maintenance` (consolidation trigger), `memory_reflection` (weekly LLM summary), and `ner` (multi-provider entity extraction) exist because the memory extension lacks an internal agent and a graph structure. In a proper architecture, consolidation produces summaries as a by-product; entity extraction is part of the write-path agent; reflection emerges from graph queries over community clusters. Three separate extensions compensate for one missing capability: an LLM-powered write-path.
3. **Retrieval is intent-blind.** All queries — "why did the project fail?", "when did we last discuss budget?", "tell me everything about Alice" — go through the same FTS5 + vector + RRF pipeline. The system cannot route a causal query to graph traversal or a temporal query to a timeline index because no graph exists.

Industry SOTA confirms the direction:

- **Zep/Graphiti** (2025): Three subgraphs (Episode, Semantic Entity, Community), bi-temporal `valid_at/invalid_at`, precomputed graph — retrieval without LLM. +18.5% on LongMemEval at -90% latency vs MemGPT.
- **Mem0** (2025): Vector + graph stores in parallel, LLM-driven write-path (Extract → Compare → ADD/UPDATE/DELETE/NOOP in one call), +26% over OpenAI Memory.
- **MAGMA** (2026): Four orthogonal graphs (semantic, temporal, causal, entity), dual-stream evolution, adaptive traversal policy.

All three converge on the same architectural pattern: **graph-structured storage, LLM on write-path only, algorithmic retrieval.** This ADR adopts that pattern within the assistant4 Extension Contract.

### Prior art

- **Research concept**: "Cognitive Memory System — Technical Implementation Concept" (Feb 2026), synthesized from 10+ SOTA systems (MAGMA, Zep/Graphiti, Hindsight/TEMPR, MemoryOS, FadeMem, A-MEM, Mem0, ES-Mem, Synapse, Better-Memory-MCP).
- **assistant3 ADR 003-memory**: First implementation attempt — graph model (`nodes + edges + entities`), intent-aware retrieval, bi-temporal versioning, consolidation pipeline. Validated the schema and retrieval strategies; revealed over-engineering in community detection and point-in-time queries.
- **assistant4 ADR 005**: Simplified the model too far — lost the graph, lost provenance, lost intent-aware retrieval.

This ADR targets the middle ground: **full structural richness (graph, types, edges, temporal, provenance) without complex graph algorithms (community detection, point-in-time queries)**. 90% of the value at 30% of the complexity.

## Decision

### 1. Core Principle: LLM on Write, Algorithms on Read

This is the single most important architectural principle. Everything else follows from it.


| Path                                          | Responsibility                                                                     | Latency      | LLM?              |
| --------------------------------------------- | ---------------------------------------------------------------------------------- | ------------ | ----------------- |
| **Hot path** (synchronous)                    | Record episode, FTS5 index, temporal edge, regex entities                          | <50ms        | No                |
| **Slow path** (async background)              | Generate embedding, fast entity linking (alias match)                              | ~200ms       | No                |
| **Write-path agent** (post-session / nightly) | Extract facts/procedures/opinions, resolve entities, detect conflicts, build edges | seconds      | Yes (cheap model) |
| **Read path** (retrieval)                     | Intent classification, FTS5 + vector + graph BFS, RRF fusion, context assembly     | <200ms P95   | No                |
| **Decay** (nightly)                           | Ebbinghaus formula, prune below threshold                                          | milliseconds | No                |


The write-path agent replaces three separate extensions (`memory_maintenance`, `memory_reflection`, `ner`) with a single LLM agent that has tools for graph manipulation. The read path is purely algorithmic — deterministic, fast, debuggable.

### 2. Memory Architecture: Session vs Long-Term

Unchanged from ADR 005: two distinct layers.


| Layer                | Implementation                    | Scope                                                                    |
| -------------------- | --------------------------------- | ------------------------------------------------------------------------ |
| **Session memory**   | OpenAI Agents SDK `SQLiteSession` | In-conversation context, per-turn history                                |
| **Long-term memory** | Memory v2 extension (this ADR)    | Cross-session knowledge: episodes, facts, procedures, opinions, entities |


This ADR covers **only long-term memory**.

### 3. Single Extension Architecture

Memory v2 is a **single extension** implementing `ToolProvider + ContextProvider + SchedulerProvider`. No satellite extensions.

```
sandbox/extensions/memory/
├── manifest.yaml      — extension metadata, schedules, agent config
├── main.py            — MemoryExtension: lifecycle, event handlers, hot/slow path
├── schema.sql         — full graph schema (nodes, edges, entities, FTS5, vec)
├── storage.py         — MemoryStorage: CRUD, graph operations, embedding index
├── retrieval.py       — intent classification, multi-strategy search, RRF, context assembly
├── agent.py           — MemoryAgent: write-path agent setup and invocation
├── decay.py           — Ebbinghaus decay and pruning (pure algorithm)
├── tools.py           — orchestrator tools + write-path agent tools
└── prompt.jinja2      — write-path agent system prompt
```

**Protocol composition:**


| Protocol            | Purpose                                                                                 |
| ------------------- | --------------------------------------------------------------------------------------- |
| `ToolProvider`      | Exposes 6 tools to the Orchestrator (search, remember, correct, confirm, entity, stats) |
| `ContextProvider`   | Injects relevant memory into agent prompts before each invocation                       |
| `SchedulerProvider` | Triggers nightly consolidation and decay via manifest cron schedules                    |


Memory no longer depends on `memory_maintenance`, `memory_reflection`, or `ner`. The `embedding` extension remains as a separate, reusable service — Memory depends on it for vector generation.

### 4. Extensions to Remove


| Extension            | Disposition                | Rationale                                                                                                                                                           |
| -------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `memory` (current)   | **Replace** with Memory v2 | Flat model, no graph, no agent                                                                                                                                      |
| `memory_maintenance` | **Remove**                 | Consolidation moves into Memory v2's `SchedulerProvider` + write-path agent                                                                                         |
| `memory_reflection`  | **Remove**                 | Reflection becomes a by-product of consolidation, stored as an opinion node                                                                                         |
| `ner`                | **Remove**                 | Only consumer was `memory`. Hot-path entity extraction (regex) moves inline. Write-path NER is handled by the memory agent's LLM call — no separate pipeline needed |
| `embedding`          | **Keep**                   | Reusable service; Memory v2 depends on it via `depends_on`                                                                                                          |


### 5. Data Schema: Graph Model

Three core tables replace the flat `memories` table. Schema deployed in full from day one (evolutionary architecture — no migrations as features are added).

```sql
-- ==========================================================================
-- Nodes: memory atoms (episodes, facts, procedures, opinions)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS nodes (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL CHECK(type IN ('episodic','semantic','procedural','opinion')),
    content          TEXT NOT NULL,
    embedding        BLOB,

    -- Bi-temporal: event time + validity interval
    event_time       INTEGER NOT NULL,    -- when the event actually occurred
    created_at       INTEGER NOT NULL,    -- when recorded in DB (ingestion time)
    valid_from       INTEGER NOT NULL,    -- start of fact validity
    valid_until      INTEGER,             -- NULL = still valid (soft-delete sets this)

    -- Confidence and lifecycle
    confidence       REAL NOT NULL DEFAULT 1.0,
    access_count     INTEGER NOT NULL DEFAULT 0,
    last_accessed    INTEGER,
    decay_rate       REAL NOT NULL DEFAULT 0.1,

    -- Provenance
    source_type      TEXT,     -- conversation | tool_result | extraction | consolidation
    source_role      TEXT,     -- user | orchestrator | <agent_id>
    session_id       TEXT,     -- links episodic nodes to their conversation session

    -- Extensible metadata
    attributes       TEXT DEFAULT '{}'
);

-- ==========================================================================
-- Edges: typed relationships between nodes
-- ==========================================================================
CREATE TABLE IF NOT EXISTS edges (
    id               TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id        TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,

    relation_type    TEXT NOT NULL CHECK(relation_type IN
        ('temporal','causal','entity','derived_from','supersedes')),
    predicate        TEXT,               -- human-readable label (e.g. "works_at", "caused_by")
    weight           REAL NOT NULL DEFAULT 1.0,
    confidence       REAL NOT NULL DEFAULT 1.0,

    valid_from       INTEGER NOT NULL,
    valid_until      INTEGER,

    evidence         TEXT DEFAULT '[]',  -- JSON: supporting episode node IDs

    created_at       INTEGER NOT NULL
);

-- ==========================================================================
-- Entity anchors: canonical identities for real-world entities
-- ==========================================================================
CREATE TABLE IF NOT EXISTS entities (
    id               TEXT PRIMARY KEY,
    canonical_name   TEXT NOT NULL,
    type             TEXT NOT NULL CHECK(type IN
        ('person','project','organization','place','concept','tool')),
    aliases          TEXT DEFAULT '[]',   -- JSON: ["Sasha", "my boss", "Alex"]
    summary          TEXT,                -- LLM-generated entity description
    embedding        BLOB,
    first_seen       INTEGER NOT NULL,
    last_updated     INTEGER NOT NULL,
    mention_count    INTEGER NOT NULL DEFAULT 1,
    attributes       TEXT DEFAULT '{}'
);

-- Node-entity junction table (indexed for fast entity-based queries)
CREATE TABLE IF NOT EXISTS node_entities (
    node_id    TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_ne_entity ON node_entities(entity_id);

-- ==========================================================================
-- Performance indexes
-- ==========================================================================
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_event_time ON nodes(event_time);
CREATE INDEX IF NOT EXISTS idx_nodes_valid ON nodes(valid_from, valid_until);
CREATE INDEX IF NOT EXISTS idx_nodes_confidence ON nodes(confidence);
CREATE INDEX IF NOT EXISTS idx_nodes_session ON nodes(session_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation_type);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_valid ON edges(valid_from, valid_until);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name);

-- ==========================================================================
-- Full-text search (FTS5, built-in)
-- ==========================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    content,
    content=nodes,
    content_rowid=rowid,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS nodes_fts_insert AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS nodes_fts_update AFTER UPDATE OF content ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
    INSERT INTO nodes_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS nodes_fts_delete AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, content)
        VALUES ('delete', old.rowid, old.content);
END;

-- ==========================================================================
-- Vector search (sqlite-vec, optional)
-- ==========================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS vec_nodes USING vec0(
    node_id TEXT PRIMARY KEY,
    embedding float[256]
);

CREATE VIRTUAL TABLE IF NOT EXISTS vec_entities USING vec0(
    entity_id TEXT PRIMARY KEY,
    embedding float[256]
);
```

#### Schema design rationale

**Why `nodes + edges` instead of flat `memories`:** Relationships are first-class data. A `supersedes` edge explicitly tracks knowledge evolution. A `derived_from` edge traces provenance. A `temporal` edge chains consecutive episodes. A `causal` edge captures cause-effect. These cannot be expressed in JSON arrays — they need indexable, queryable, typed edges.

**Why 4 node types instead of `kind` field with arbitrary values:** `episodic`, `semantic`, `procedural`, `opinion` map to cognitive science categories with distinct lifecycle rules. Episodic nodes never decay (immutable audit trail). Semantic nodes are durable facts subject to consolidation. Procedural nodes capture learned action patterns. Opinion nodes track preferences with dynamic confidence. The `CHECK` constraint enforces this taxonomy.

**Why 5 edge types (no `semantic` edge from the concept):** The concept defined 6 edge types including `semantic` (cosine similarity links between nodes). We drop `semantic` edges because vector search via `vec_nodes` already provides semantic similarity at query time — precomputing and maintaining similarity edges is expensive and redundant. The remaining 5 types (`temporal`, `causal`, `entity`, `derived_from`, `supersedes`) each carry information that cannot be derived from embeddings alone.

**Why `node_entities` junction table instead of `entity_ids` JSON:** ADR 005 stored entity links as `entity_ids TEXT DEFAULT '[]'` JSON. Querying "all memories about entity X" required `WHERE entity_ids LIKE '%"uuid"%'` — a full table scan. The junction table with index provides O(log n) lookup.

**Why bi-temporal (`event_time` + `valid_from/valid_until`) but no point-in-time queries:** The fields exist and are populated correctly (e.g., importing a historical email sets `event_time` to the email's date, `created_at` to now, `valid_from` to now). However, we do **not** implement temporal graph queries like "what did the agent know at time T" — the SQL complexity in SQLite is disproportionate to the value for a single-user agent. The fields are forward-compatible: if needed later, the schema already supports it.

**Why no `extraction_method` column:** The concept included `extraction_method` (direct | llm_extraction | rule_based). In practice, `source_type` (conversation | tool_result | extraction | consolidation) plus `derived_from` edges fully capture provenance. The extraction method is metadata on the edge, not the node.

### 6. SQLite Concurrency Model

Memory v2 runs hot path, slow path, write-path agent, decay, and retrieval — all in the same process, with `asyncio.create_task` for background work. SQLite is single-writer; concurrent writes from multiple async tasks cause `database is locked` errors.

**Design:** Single writer task with serialized write queue; parallel read connections.

```
┌─────────────┐     ┌──────────────┐     ┌───────────┐
│  Hot path   │──┐  │  Slow path   │──┐  │  Agent    │──┐
│  (writes)   │  │  │  (writes)    │  │  │  (writes) │  │
└─────────────┘  │  └──────────────┘  │  └───────────┘  │
                 ▼                    ▼                 ▼
           ┌──────────────────────────────────────────────┐
           │          asyncio.Queue (write ops)           │
           └────────────────────┬─────────────────────────┘
                                ▼
                    ┌───────────────────────┐
                    │  Writer task (single) │  ← sequential apply
                    │  conn with WAL mode   │
                    └───────────────────────┘

┌─────────────┐     ┌──────────────┐
│  Retrieval  │     │ ContextProv. │     ← parallel reads
│  (reads)    │     │  (reads)     │       (separate connections)
└─────────────┘     └──────────────┘
```

**Implementation rules:**

1. **One write connection** (WAL mode, `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL`), owned by the writer task. All INSERT/UPDATE/DELETE operations are submitted as callables to an `asyncio.Queue` and applied sequentially by the writer task.
2. **Read connections** are opened as needed (or pooled). Multiple reads can proceed in parallel with WAL. Read connections are read-only (`PRAGMA query_only=ON`).
3. **Hot path** submits write ops to the queue and does not `await` their completion — fire-and-forget for the <50ms target. The episodic node ID is generated before submission, so the caller has it immediately.
4. **Slow path and write-path agent** submit writes and `await` the result via `asyncio.Future` attached to each queue item.

This pattern is standard for async SQLite (same approach used by the EventBus journal in ADR 004) and prevents contention without external locking.

### 7. Five Edge Types


| Edge Type      | Links                                  | Purpose                                                   | Created By                                                    |
| -------------- | -------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------- |
| `temporal`     | Episode → Episode                      | Consecutive events in time                                | Hot path (auto, on each new episode)                          |
| `causal`       | Episode → Episode                      | Cause-effect relationship                                 | Write-path agent (LLM inference)                              |
| `entity`       | Node → Entity proxy node               | Links any node to an entity anchor                        | Hot path (regex) + write-path agent (LLM)                     |
| `derived_from` | Semantic/Procedural/Opinion → Episodic | Provenance: "this fact was extracted from these episodes" | Write-path agent (during consolidation)                       |
| `supersedes`   | New node → Old node                    | Knowledge evolution: "new fact replaces old fact"         | Write-path agent (conflict resolution) or `correct_fact` tool |


`entity` edges link nodes to entity anchors indirectly through the `node_entities` junction table for query efficiency. The `edges` table stores `entity` edges when there is a predicate (e.g., `(User) -[works_at]-> (CompanyX)`), while the junction table provides fast "all nodes for entity X" lookups.

### 8. Hot Path (<50ms, No LLM)

Triggered on every `user_message` and `agent_response` event. Synchronous, must not block the agent.

```
Event arrives (user_message | agent_response)
  │
  ├─ 1. Create episodic node
  │     content = message text
  │     type = 'episodic'
  │     event_time = created_at = now
  │     valid_from = now, valid_until = NULL
  │     session_id = current session ID
  │     source_role = 'user' | 'orchestrator'
  │
  ├─ 2. FTS5 indexing (automatic via trigger)
  │
  ├─ 3. Temporal edge (link to previous episode in session)
  │     SELECT id FROM nodes
  │       WHERE type = 'episodic' AND session_id = ?
  │       ORDER BY event_time DESC LIMIT 1
  │     → INSERT edge(prev → new, relation_type='temporal')
  │
  └─ 4. Schedule slow path (asyncio.create_task)
```

No embedding generation, no LLM calls, no entity extraction in the hot path. The FTS5 trigger fires automatically on INSERT. The temporal edge is a single indexed query + insert.

### 9. Slow Path (~200ms, No LLM)

Runs as an async task immediately after the hot path. Non-blocking.

```
Slow path (async)
  │
  ├─ 1. Generate embedding via embedding extension
  │     embedding = await embedding_ext.embed(content)
  │     UPDATE nodes SET embedding = ? WHERE id = ?
  │     INSERT INTO vec_nodes(node_id, embedding) VALUES (?, ?)
  │
  └─ 2. Fast entity extraction (regex only)
        Extract: capitalized names, URLs, emails, @mentions, #hashtags
        For each mention:
          → Alias match against entities table (exact match on canonical_name or aliases)
          → If match: INSERT INTO node_entities, UPDATE mention_count
          → If no match: skip (write-path agent handles new entities via LLM)
```

Regex entity extraction in the slow path is deliberately conservative — it only links to **existing** entity anchors via exact alias matching. Creating new entities from ambiguous mentions is deferred to the write-path agent, which can use LLM context to distinguish "Warsaw" (city) from "Warsaw" (project codename).

### 10. Write-Path Agent (Post-Session / Nightly)

A single LLM agent (cheap model: GPT-4o-mini or equivalent local model) replaces the logic previously spread across `memory_maintenance`, `memory_reflection`, and `ner`. The agent is invoked in two modes.

#### Model Resolution

The write-path agent is a **private `Agent` instance** inside the memory extension — not an `AgentProvider` (see Alternatives Considered). It obtains its LLM model through the standard `ModelRouter` mechanism, the same way heartbeat, memory_maintenance, and all other extensions resolve models:

```python
# In main.py initialize():
model = context.model_router.get_model("memory_agent")
self._write_agent = Agent(
    name="MemoryWritePathAgent",
    instructions=self._load_instructions(),
    model=model,
    tools=self._build_write_path_tools(),
    output_type=ConsolidationResult,
)
```

The `"memory_agent"` identifier maps to the `agent_config` block in the manifest (§16):

```yaml
agent_config:
  memory_agent:
    model: gpt-5-mini
```

At startup, the Loader registers this config with `ModelRouter.register_agent_config("memory_agent", {...})`. The `ModelRouter` then resolves the provider and API key from the global `config/settings.yaml`. **The memory extension never handles API keys, provider URLs, or SDK client construction directly.** When the user switches providers (e.g., OpenAI → Anthropic → local Ollama), the `ModelRouter` returns the correct SDK Model instance — no memory extension code changes required.

This is the same pattern used by `heartbeat` (`model_router.get_model("heartbeat_scout")`) and the former `memory_maintenance` (`model_router.get_model(context.agent_id)`). The difference is that `heartbeat` is a `SchedulerProvider` with a public cron task, while the memory write-path agent is invoked only by `MemoryExtension` internally — it has no public `invoke()` method and no `AgentDescriptor`.

#### 10.1 Post-Session Consolidation

Triggered when a session change is detected (see §15) or by nightly maintenance for missed sessions.

**Idempotency protocol:** The write-path agent follows a strict check-before-run pattern to ensure safe retries if interrupted:

```
consolidate_session(session_id):
  │
  ├─ 1. CHECK: is_session_consolidated(session_id)?
  │     → If yes: skip (return early, no work)
  │     → If no: proceed
  │
  ├─ 2. Fetch episodic nodes for session (paginated)
  ├─ 3. Extract semantic facts (durable knowledge about the user/world)
  ├─ 4. Extract procedural patterns (successful action sequences)
  ├─ 5. Extract opinions/preferences (subjective assessments)
  ├─ 6. For each extracted node:
  │     ├─ Save node with derived_from edges to source episodes
  │     ├─ Extract entities (LLM-powered NER)
  │     ├─ Resolve entities to existing anchors or create new ones
  │     └─ Detect and resolve conflicts with existing facts
  │
  └─ 7. COMMIT: mark_session_consolidated(session_id)
         (only after all operations complete successfully)
```

If the agent is interrupted between steps 2-6, the session remains unconsolidated. The next invocation (nightly maintenance or retry) re-runs from step 1, detects `consolidated = false`, and re-processes. Duplicate fact extraction is handled by deduplication (embedding similarity check against existing facts for the same session).

The agent has tools for each of these operations. It decides what to extract, how to phrase facts, and how to resolve conflicts — all in natural language via LLM. This replaces hundreds of lines of hardcoded extraction logic with a prompt and 6-8 tools.

#### 10.2 Nightly Maintenance

Triggered by cron schedule (e.g., `0 3 * * *`). Runs consolidation for any missed sessions, then performs maintenance:

```
Nightly maintenance (scheduled)
  │
  ├─ 1. Consolidate any pending sessions (same as 10.1)
  ├─ 2. Apply Ebbinghaus decay (algorithmic, no LLM)
  │     confidence_new = confidence_old × exp(-λ × (days_since_access)^0.8)
  │     Prune nodes below threshold (default 0.05) via soft-delete
  └─ 3. Entity enrichment (LLM)
        For entities with sparse summaries:
          Agent generates/updates entity summary
          Re-embeds entity for improved vector search
```

**Causal inference** (analyzing consecutive episodes and creating `causal` edges) is **deferred to Phase 5** (see Implementation Phases). Causal inference from episode pairs is the most LLM-dependent and hallucination-prone task in the system. Temporal edges (deterministic, created automatically in the hot path) provide 80% of the timeline value without any LLM risk. Adding causal edges on top is an incremental improvement that can be tuned independently once the base system is stable.

#### 10.3 Write-Path Agent Tools

These tools are **not exposed to the Orchestrator** — they are internal to the memory agent.


| Tool                        | Description                                                                  |
| --------------------------- | ---------------------------------------------------------------------------- |
| `get_session_episodes`      | Fetch episodic nodes for a session (paginated)                               |
| `save_nodes_batch`          | Save extracted nodes (semantic/procedural/opinion) with `derived_from` edges |
| `extract_and_link_entities` | LLM-powered NER + entity resolution for a batch of nodes                     |
| `detect_conflicts`          | Find potentially contradicting facts via hybrid search                       |
| `resolve_conflict`          | Create `supersedes` edge, adjust confidence scores                           |
| `mark_session_consolidated` | Set consolidation flag for a session                                         |
| `is_session_consolidated`   | Check if session was already consolidated (idempotency guard)                |
| `save_causal_edges`         | Create `causal` edges between episode pairs (Phase 5+)                       |
| `update_entity_summary`     | Regenerate entity summary and re-embed                                       |


#### 10.4 Cost Estimate

Per consolidation call (30 episodes, GPT-4o-mini):

- Input: ~2.5K tokens (episodes + system prompt + existing facts for conflict detection)
- Output: ~500 tokens (extracted facts/entities as JSON)
- Cost: ~$0.001

At 50 sessions/day: **~$0.05/day**. With a local model (32K context): **free**.

### 11. Intent-Aware Retrieval

The read path classifies query intent and routes to the optimal search strategy. No LLM on the read path — classification is algorithmic.

#### 11.1 Intent Classification

Intent classification determines which retrieval strategy to prioritize. The classifier is behind a **strategy interface** with switchable implementations:

```python
class IntentClassifier(ABC):
    """Strategy interface for intent classification."""

    @abstractmethod
    def classify(self, query: str) -> str:
        """Return intent: 'why' | 'when' | 'who' | 'what' | 'general'."""
```

Two implementations are provided from Phase 2 (when embeddings become available):

**Primary: `EmbeddingIntentClassifier`** — cosine similarity against pre-embedded intent exemplars. Multilingual from day one (inherits language support from the embedding model). Zero extra LLM cost — the query embedding is already computed for vector search and reused here.

```python
class EmbeddingIntentClassifier(IntentClassifier):
    """Cosine similarity against intent exemplars. Multilingual, <2ms."""

    EXEMPLARS: dict[str, list[str]] = {
        "why": [
            "why did this happen", "what caused the failure",
            "what is the reason", "explain the cause",
            "почему это произошло", "в чем причина", "из-за чего",
        ],
        "when": [
            "when did we discuss", "what happened after",
            "timeline of events", "before the meeting",
            "когда мы обсуждали", "после встречи", "хронология событий",
        ],
        "who": [
            "who is responsible", "who said that", "whose idea",
            "кто отвечает за", "чья это идея", "кто сказал",
        ],
        "what": [
            "what do you know about", "tell me everything about",
            "what is the status", "which project",
            "что ты знаешь о", "расскажи всё о", "какой статус",
        ],
    }

    def __init__(self, embed_fn: Callable, threshold: float = 0.45):
        self._embed_fn = embed_fn
        self._threshold = threshold
        self._intent_embeddings: dict[str, list] = {}

    async def initialize(self) -> None:
        """Pre-embed exemplars at startup. One-time cost."""
        for intent, examples in self.EXEMPLARS.items():
            self._intent_embeddings[intent] = [
                await self._embed_fn(ex) for ex in examples
            ]

    def classify(self, query: str, query_embedding: list[float] | None = None) -> str:
        """Classify intent. Accepts pre-computed query embedding to avoid redundant work."""
        if query_embedding is None:
            return "general"  # embedding unavailable, fallback
        best_intent, best_score = "general", 0.0
        for intent, embs in self._intent_embeddings.items():
            score = max(cosine_sim(query_embedding, e) for e in embs)
            if score > best_score:
                best_intent, best_score = intent, score
        return best_intent if best_score > self._threshold else "general"
```

The exemplar set is a Python dict, not a config file — it's part of the implementation. Adding new languages or refining exemplars is a code change, not a config change, because exemplar quality directly affects retrieval strategy routing. The `threshold` (default 0.45) is configurable via manifest (§16) for tuning without code changes.

**Fallback: `KeywordIntentClassifier`** — regex-based, English-only. Used in Phase 1 (before embeddings are available) and as a fallback when the embedding extension is unavailable (graceful degradation):

```python
class KeywordIntentClassifier(IntentClassifier):
    """Regex keyword matching. English-only, <1ms. Fallback classifier."""

    def classify(self, query: str) -> str:
        q = query.strip().lower()
        if re.search(r'\b(why|cause|caused|reason|because|led to|resulted in)\b', q):
            return 'why'
        if re.search(r'\b(when|after|before|during|timeline|sequence|then|next|previous)\b', q):
            return 'when'
        if re.search(r'\b(who|whom|whose)\b', q):
            return 'who'
        if re.search(r'\b(what|which|everything about|tell me about)\b', q):
            return 'what'
        return 'general'
```

**Selection logic in `main.py`:**

```python
if self._embedding_available:
    self._intent_classifier = EmbeddingIntentClassifier(
        embed_fn=self._embedding_ext.embed,
        threshold=self._ctx.get_config("intent_similarity_threshold", 0.45),
    )
    await self._intent_classifier.initialize()
else:
    self._intent_classifier = KeywordIntentClassifier()
```

**Design rationale:** The embedding-based classifier satisfies "Algorithms on Read" (no LLM call), is multilingual by construction (embedding models like `text-embedding-3-small` or `multilingual-e5` support 100+ languages), and adds negligible latency (~1-2ms for 28 cosine similarities). The keyword classifier remains as the Phase 1 / degraded-mode fallback — it works for English-only testing but is not the production path.

#### 11.2 Search Strategy Routing


| Intent         | Primary Strategy                                                 | Fallback                 |
| -------------- | ---------------------------------------------------------------- | ------------------------ |
| `why`          | Causal graph BFS (traverse `causal` edges from seed nodes)       | + hybrid (FTS5 + vector) |
| `when`         | Temporal chain traversal (follow `temporal` edges) + time filter | + hybrid                 |
| `who` / `what` | Entity lookup (resolve entity → follow `entity` edges)           | + hybrid                 |
| `general`      | Full hybrid search (FTS5 + vector + entity)                      | —                        |


All strategies produce ranked node lists. Results are fused via **Reciprocal Rank Fusion** (RRF):

```
Score(node) = Σ w_m / (k + rank_m(node)) for each method m
```

Where `k` and per-method weights `w_m` are configurable via manifest:


| Parameter           | Default | Rationale                                                                                                                                                                                                                                                        |
| ------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rrf_k`             | 60      | Standard RRF constant. The canonical value was derived for web-scale document retrieval; memory nodes (short facts, episodes) may benefit from a lower `k` (e.g., 20-40) that amplifies top-rank differences. Exposed as config for tuning without code changes. |
| `rrf_weight_vector` | 1.0     | Weight for vector search results in RRF fusion                                                                                                                                                                                                                   |
| `rrf_weight_fts`    | 1.0     | Weight for FTS5 keyword results. For short factual nodes, FTS5 exact matches may deserve higher weight than semantic similarity.                                                                                                                                 |
| `rrf_weight_graph`  | 1.0     | Weight for graph traversal results                                                                                                                                                                                                                               |


#### 11.3 Graph Traversal

Causal and temporal traversal use recursive CTEs in SQLite — no external graph engine:

```sql
-- Causal chain: find causes of a node (BFS, max depth 3)
WITH RECURSIVE causal_chain(node_id, depth) AS (
    SELECT source_id, 1 FROM edges
    WHERE target_id = ? AND relation_type = 'causal' AND valid_until IS NULL
  UNION ALL
    SELECT e.source_id, cc.depth + 1 FROM edges e
    JOIN causal_chain cc ON e.target_id = cc.node_id
    WHERE e.relation_type = 'causal' AND e.valid_until IS NULL AND cc.depth < 3
)
SELECT DISTINCT n.* FROM nodes n
JOIN causal_chain cc ON n.id = cc.node_id
WHERE n.valid_until IS NULL
ORDER BY n.event_time DESC;
```

Temporal traversal follows `temporal` edges in the same pattern. Entity traversal joins through `node_entities`.

#### 11.4 Context Assembly

The final context pack for the LLM is assembled with token budgeting:


| Section              | Budget Share | Priority | Content                                                   |
| -------------------- | ------------ | -------- | --------------------------------------------------------- |
| **Facts**            | 40%          | Highest  | Relevant semantic nodes, sorted by RRF score × confidence |
| **Entity profiles**  | 25%          | High     | Summaries of mentioned entities                           |
| **Temporal context** | 25%          | Medium   | Recent relevant episodes for chronological grounding      |
| **Evidence**         | 10%          | Low      | Source quotes for the most important facts                |


Each section is trimmed to its budget share. Overflow is discarded from lowest-priority items.

#### 11.5 Adaptive Query Complexity

Query complexity determines retrieval depth and token budget. Classification is heuristic-based:


| Complexity  | Detection Heuristic                                                                                                                                       | Token Budget | Retrieval Depth       |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ | --------------------- |
| **Simple**  | `len(query.split()) < 10` AND no multi-clause conjunctions AND no aggregate keywords                                                                      | 1000         | Top 5, graph depth 2  |
| **Complex** | `len(query.split()) >= 10` OR contains aggregate keywords (`compare`, `summarize`, `everything`, `all`, `overview`) OR 2+ conjunctions (`and`/`or`/`but`) | 3000         | Top 20, graph depth 4 |


The thresholds (word count, keyword lists) are implementation details and may be tuned. The interface is `classify_query_complexity(query) -> 'simple' | 'complex'` — same pluggable pattern as intent classification.

### 12. Confidence, Decay, and Knowledge Evolution

#### 12.1 Confidence Scoring


| Factor                 | Impact                                                           |
| ---------------------- | ---------------------------------------------------------------- |
| **Source reliability** | User statement → 1.0; LLM extraction → 0.8; inference → 0.6      |
| **Confirmation**       | `confirm_fact` tool → confidence = 1.0, decay_rate = 0.0         |
| **Contradiction**      | `resolve_conflict` → old fact confidence = 0.3, decay_rate = 0.5 |
| **Access frequency**   | Reinforcement: `Δ = 0.05 × log(1 + access_count / 20)`           |


#### 12.2 Ebbinghaus Decay

Applied nightly to all non-protected semantic, procedural, and opinion nodes:

```
confidence(t) = confidence₀ × exp(-λ × (t - t_last_access)^0.8)
```

Where:

- `λ` = `decay_rate` (per-node, default 0.1; 0.0 for protected nodes)
- `0.8` = sub-exponential exponent (slower than pure exponential, models human forgetting)

**Protection rules:**

- Entity anchors with `mention_count > N` (configurable): `decay_rate = 0.0`
- User-confirmed facts (`confirm_fact`): `decay_rate = 0.0`
- Episodic nodes **never decay** — immutable audit trail. This means episodic nodes and their FTS5 entries accumulate indefinitely. At 50K-100K episodes (roughly one year of active use), FTS5 performance remains acceptable (sub-second), but the index size grows linearly. To prevent episodic noise in retrieval, `ContextProvider` and `search_memory` default to `type != 'episodic'` unless explicitly requested (see §14).
- Nodes below threshold (default 0.05): soft-deleted (`valid_until = now`)

#### 12.3 Knowledge Evolution

When new information contradicts an existing fact:

1. Write-path agent detects conflict via hybrid search.
2. Old fact's `valid_until` is set to now (soft-delete).
3. New fact node is created with `valid_from = now`.
4. `supersedes` edge links new → old (with evidence).
5. Old fact's confidence drops to 0.3, decay_rate to 0.5.
6. Full history preserved — nothing is physically deleted.

The `correct_fact` Orchestrator tool follows the same protocol, triggered explicitly by the user.

### 13. Orchestrator Tools (6 core)

Exposed to the Orchestrator via `ToolProvider.get_tools()`. Kept minimal to avoid system prompt bloat (ADR 003 rationale: tool explosion degrades LLM routing quality).


| Tool              | Description                                                     | When to Use                                           |
| ----------------- | --------------------------------------------------------------- | ----------------------------------------------------- |
| `search_memory`   | Hybrid search with intent routing (FTS5 + vector + graph + RRF) | Any memory query. Primary tool.                       |
| `remember_fact`   | Explicitly save a fact, create entity links                     | Agent notices important information worth remembering |
| `correct_fact`    | Supersede old fact with new version                             | User corrects a remembered fact                       |
| `confirm_fact`    | Protect fact from decay (confidence=1.0, decay_rate=0)          | User confirms a fact is accurate                      |
| `get_entity_info` | Entity profile: summary, related facts, timeline                | "Tell me everything about X"                          |
| `memory_stats`    | Graph-level metrics (see below)                                 | Diagnostics, "how much do you remember?", debugging   |


`search_memory` accepts filters: `type` (episodic/semantic/procedural/opinion), `entity_name`, `after`/`before` (time expressions), `limit`.

**Timestamp output contract** (see ADR 009): each result dict contains the raw `event_time` integer plus four display-only string fields:

| Field | Example | Notes |
| --- | --- | --- |
| `event_time_iso` | `2026-02-23T15:23:47+00:00` | RFC 3339 UTC |
| `event_time_local` | `2026-02-23 18:23:47 UTC+3` | Host system local timezone |
| `event_time_tz` | `UTC+3` | Timezone label |
| `event_time_relative` | `3 hours ago` | Relative time via `humanize` |

All four fields are empty strings when `event_time` is missing or zero. The `get_timeline` tool's `timestamp` field also uses `event_time_iso` format. Formatting logic lives in `core/utils/formatting.py` (shared across extensions).

`**memory_stats` output (Phase 6):**

```python
{
    "nodes": {"episodic": 1234, "semantic": 89, "procedural": 12, "opinion": 34},
    "edges": {"temporal": 1233, "causal": 8, "entity": 156, "derived_from": 135, "supersedes": 7},
    "entities": 45,
    "orphan_nodes": 3,            # nodes with no edges (potential data quality issue)
    "avg_edges_per_node": 2.1,
    "unconsolidated_sessions": 2, # sessions awaiting write-path agent
    "last_consolidation": "2026-02-23T03:00:12Z",
    "last_decay_run": "2026-02-23T03:01:45Z",
    "storage_size_mb": 12.4
}
```

### 14. Context Provider Integration

Memory v2 implements `ContextProvider` to inject relevant knowledge into the agent's prompt before every invocation. This is how the Orchestrator and Heartbeat access memory without explicit tool calls.

```python
@property
def context_priority(self) -> int:
    return 50

async def get_context(self, prompt: str, *, agent_id: str | None = None) -> str | None:
    complexity = classify_query_complexity(prompt)
    params = get_adaptive_params(complexity)

    results = await self._retrieval.search(
        query=prompt,
        intent_classifier=self._intent_classifier,
        limit=params['limit'],
        token_budget=params['token_budget'],
    )

    if not results:
        return None

    return self._retrieval.assemble_context(results, token_budget=params['token_budget'])
```

The `search()` method internally computes the query embedding (for vector search), then passes it to `self._intent_classifier.classify(query, query_embedding)` — a single embedding call serves both purposes.

`**context_priority = 50`:** Memory v2 runs before most other `ContextProvider` extensions (default priority is 100). Memory context should be injected early because other providers (e.g., Heartbeat, task schedulers) may benefit from memory-enriched prompts. The value 50 is chosen to leave room for higher-priority providers (e.g., system state at priority 10-30) if they emerge. Currently no other `ContextProvider` exists in the system, so there is no conflict.

**Default type filtering:** `ContextProvider.get_context()` retrieves only `semantic`, `procedural`, and `opinion` nodes by default — not raw `episodic` nodes. Episodic nodes are verbose (full conversation messages) and accumulate indefinitely without decay. Injecting them into the context prompt would consume the token budget with low-value raw text. The Orchestrator can still access episodic nodes explicitly via `search_memory(type='episodic')` when the user asks for conversation history.

**Heartbeat synergy:** The Heartbeat extension runs a Scout agent every 2 minutes with `ctx.enrich_prompt(prompt)`. With intent-aware retrieval, a Heartbeat prompt like "Are there pending tasks or follow-ups?" naturally retrieves recent temporal chains and entity-linked facts — identifying items that require attention without specialized Heartbeat-memory integration code.

### 15. Session Lifecycle and Consolidation Trigger

#### Event Subscriptions


| Event               | Mechanism                                               | Purpose                                                 |
| ------------------- | ------------------------------------------------------- | ------------------------------------------------------- |
| `user_message`      | `context.subscribe("user_message", handler)`            | Hot path: save user episodes                            |
| `agent_response`    | `context.subscribe("agent_response", handler)`          | Hot path: save agent episodes                           |
| `session.completed` | `context.subscribe_event("session.completed", handler)` | Trigger write-path agent for post-session consolidation |


#### Core Change: Inactivity-Based Session Rotation

**Problem:** Currently, `core/runner.py` generates `session_id` once at process startup (`f"orchestrator_{int(time.time())}"`) and never changes it. The `MessageRouter` passes this static ID in every `user_message` and `agent_response` event. This means Memory v2's session-change detection never triggers — all episodes belong to one infinite session.

**Decision:** `MessageRouter` gains inactivity-based session rotation. The kernel already owns `session_id` lifecycle (creates it, passes it to `SQLiteSession`, injects it into events). Adding rotation is a natural extension of existing responsibility.

**Mechanism:**

```python
# In MessageRouter (core/extensions/router.py):
_DEFAULT_SESSION_TIMEOUT = 1800  # 30 minutes

async def handle_user_message(self, text: str, user_id: str, channel: ChannelProvider) -> None:
    now = time.time()
    if self._last_message_at and (now - self._last_message_at) > self._session_timeout:
        await self._rotate_session()
    self._last_message_at = now
    await self._emit("user_message", {..., "session_id": self._session_id})
    # ... existing invoke_agent + agent_response ...

async def _rotate_session(self) -> None:
    old_id = self._session_id
    self._session_id = f"orchestrator_{int(time.time())}"
    self._session = SQLiteSession(self._session_id, self._session_db_path)
    if self._event_bus:
        await self._event_bus.publish(
            "session.completed",
            "kernel",
            {"session_id": old_id, "reason": "inactivity_timeout"},
        )
```

**Design details:**

1. **Inactivity timeout** is configurable via `settings.yaml` (`session.timeout_sec`, default 1800). Stored in `MessageRouter._session_timeout`.
2. **`SQLiteSession` rotation:** On session change, a new `SQLiteSession` is created for the Agents SDK, giving the Orchestrator fresh short-term conversation context. The old session DB is not deleted — the Agents SDK manages its own cleanup.
3. **`session.completed` event** is published via EventBus when a session rotates. This is a kernel-guaranteed event — Memory v2 subscribes to it as the primary consolidation trigger.
4. **Future extension points:** User-initiated session reset (via command or tool), channel-specific session boundaries, and other rotation triggers can be added to `MessageRouter` without changing the memory extension. The contract is: `session_id` changes in events → memory detects the change and consolidates.

#### Session Change Detection in Memory v2

Memory v2 detects session boundaries via two complementary mechanisms:

**Primary: `session.completed` EventBus event.** Published by the kernel when `MessageRouter` rotates the session. Memory v2 subscribes and triggers consolidation:

```python
async def _on_session_completed(self, event: Event) -> None:
    session_id = event.payload.get("session_id")
    if session_id:
        asyncio.create_task(self._consolidate_session(session_id))
```

**Secondary: `session_id` change in `user_message` events.** The hot-path handler tracks the current `session_id`. When a new `session_id` appears, the previous session is consolidated. This acts as a fallback if the EventBus event is missed:

```python
async def _on_user_message(self, data: dict) -> None:
    session_id = data.get("session_id")
    if self._current_session_id and session_id != self._current_session_id:
        asyncio.create_task(
            self._consolidate_session(self._current_session_id)
        )
    self._current_session_id = session_id
    await self._hot_path_ingest(data)
```

**Tertiary: Nightly sweep.** The nightly maintenance task (§10.2) consolidates any sessions that were missed by both mechanisms above (e.g., process crash after the last message, no subsequent message to trigger detection).

### 16. Manifest

```yaml
id: memory
name: Memory v2
version: "2.0.0"

entrypoint: main:MemoryExtension

description: |
  Graph-based cognitive memory. Stores episodes, facts, procedures, opinions
  as nodes with typed relationships (temporal, causal, entity, provenance).
  Intent-aware hybrid retrieval (FTS5 + vector + graph BFS + RRF).
  LLM-powered write path for consolidation and entity resolution.

depends_on:
  - embedding

config:
  embedding_dimensions: 256
  decay_threshold: 0.05
  decay_rate_default: 0.1
  consolidation_episodes_per_chunk: 30
  conflict_min_confidence: 0.8
  context_token_budget: 2000
  rrf_k: 60
  rrf_weight_vector: 1.0
  rrf_weight_fts: 1.0
  rrf_weight_graph: 1.0
  intent_similarity_threshold: 0.45

agent_config:
  memory_agent:
    model: gpt-5-mini

schedules:
  - name: nightly_maintenance
    cron: "0 3 * * *"
    task: run_nightly_maintenance
    description: "Consolidate pending sessions, apply decay, enrich entities"

enabled: true
```

### 17. Graceful Degradation

Each capability layer degrades independently:


| Component                                           | If unavailable                       | Fallback                                                                                                                                                               |
| --------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `embedding` extension                               | No vector search                     | FTS5 keyword search + entity lookup. Intent classifier falls back to `KeywordIntentClassifier` (English-only regex).                                                   |
| `embed_batch()` in embedding ext                    | No batch embedding                   | Memory falls back to sequential `embed()` calls. Consolidation is slower (~2-6s vs ~300ms) but functionally identical.                                                 |
| `sqlite-vec`                                        | No ANN index                         | FTS5 + entity lookup (embeddings stored in BLOB but not indexed)                                                                                                       |
| LLM for write-path agent                            | No consolidation, no LLM-powered NER | Hot path still records episodes; regex entities still link; decay still runs. Consolidation is deferred until LLM is available.                                        |
| `session.completed` event (core C1)                 | No event-driven consolidation        | Memory detects session changes via `session_id` diff in `user_message` events (secondary). Nightly cron catches remaining unconsolidated sessions (tertiary).          |


The system is fully functional with just SQLite + FTS5, degrading gracefully as each capability layer is added.

### 18. Migration from Memory v1

**No backward compatibility. No data migration.**

1. Remove `memory_maintenance`, `memory_reflection`, `ner` extension directories.
2. Remove old `memory` extension directory.
3. Deploy new `memory` extension with full schema.
4. Delete old memory database (`sandbox/data/memory/`).
5. Update `heartbeat` manifest: remove `depends_on` references to `memory_maintenance` and `memory_reflection`.

The agent starts fresh with an empty memory. Episodic nodes accumulate from the first conversation. Consolidation runs after the first completed session.

## Core Changes Required

This ADR introduces two changes outside the memory extension boundary. Both are scoped, non-breaking optimizations that reduce coupling rather than increase it.

### C1. Session Rotation in `MessageRouter`

**What changes:** `core/extensions/router.py` — `MessageRouter` gains inactivity-based session rotation (detailed in §15).

**Scope of change:**

| File | Change |
| --- | --- |
| `core/extensions/router.py` | Add `_last_message_at`, `_session_timeout` fields. In `handle_user_message()`, compare elapsed time → call `_rotate_session()` if exceeded. `_rotate_session()` creates new `session_id` + `SQLiteSession`, publishes `session.completed` via EventBus. |
| `core/events/topics.py` | Register `session.completed` topic (one line). |
| `core/settings.py` | Add `session.timeout_sec` default (1800). |

**Why in core:** The kernel already owns `session_id` lifecycle — it creates the ID, passes it to `SQLiteSession`, and injects it into `user_message`/`agent_response` events. Session rotation is a natural extension of this existing responsibility. If rotation lived in the memory extension, the extension would need to fabricate new `session_id` values and somehow propagate them back to the kernel — increasing coupling.

**Backward-compatible:** Extensions that ignore `session.completed` are unaffected. The `session_id` format does not change. `SQLiteSession` rotation only affects the Agents SDK's short-term conversation buffer — the Orchestrator's behavior is identical from the user's perspective.

**Forward extension points:** The `_rotate_session()` method can be called from other triggers in the future: user-initiated reset command, channel-specific boundaries, or explicit API calls. The mechanism is generic; inactivity timeout is simply the first trigger.

### C2. Batch Embedding in `embedding` Extension

**What changes:** `sandbox/extensions/embedding/main.py` — add `embed_batch()` alongside existing `embed()`.

**Scope of change:**

```python
async def embed_batch(
    self,
    texts: list[str],
    *,
    model: str | None = None,
    dimensions: int | None = None,
) -> list[list[float] | None]:
    """Batch-embed multiple texts in a single API call.

    Returns a list parallel to `texts`: each element is an embedding vector or
    None if that specific text was empty / failed.
    Falls back to sequential embed() calls if the provider doesn't support batching.
    """
```

**Why:** Memory v2's write-path agent processes 10-30 nodes per consolidation run. Sequential `embed()` calls mean 10-30 API round-trips (~200ms each) = 2-6 seconds of pure I/O wait. A single `embed_batch()` call reduces this to one round-trip (~300ms). The OpenAI embeddings API already accepts `input: list[str]` — this is a thin wrapper over existing capability.

**Backward-compatible:** `embed()` remains unchanged. Extensions that don't need batching are unaffected. Memory v2 calls `embed_batch()` when available, falls back to sequential `embed()` if the method doesn't exist (e.g., older embedding extension version):

```python
embed_fn = getattr(embedding_ext, "embed_batch", None)
if embed_fn:
    vectors = await embed_fn(texts)
else:
    vectors = [await embedding_ext.embed(t) for t in texts]
```

## Implementation Phases


| Phase                                         | Scope                                                                                                                                                                                                   | Duration | Outcome                                                                                                 |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------- |
| **1. Foundation + Core**                      | Schema, hot path (episodes + FTS5 + temporal edges), SQLite writer queue, `search_memory` (FTS5 only), `ContextProvider`, `KeywordIntentClassifier` as interim classifier. **Core change C1:** session rotation in `MessageRouter` + `session.completed` event topic. | 2-3 days | Agent remembers conversations, keyword search works, Heartbeat gets context, sessions rotate on inactivity |
| **2. Semantic Search + Batch Embedding**      | Embedding integration, vec_nodes index, hybrid search (FTS5 + vector + RRF), `EmbeddingIntentClassifier` (pre-embed exemplars, multilingual intent routing), `remember_fact`, `correct_fact`, `confirm_fact`. **Core change C2:** `embed_batch()` in embedding extension. | 2-3 days | Semantic similarity search, multilingual intent-aware retrieval, batch embedding reduces consolidation I/O |
| **3. Write-Path Agent**                       | Memory agent with tools (via `ModelRouter`), post-session consolidation (with idempotency protocol), entity extraction + resolution, conflict detection + resolution. Uses `embed_batch()` from Phase 2. | 3-4 days | Automatic fact extraction, entity linking, knowledge evolution                                          |
| **4. Intent-Aware Retrieval — Graph Strategies** | Temporal chain traversal, entity-based lookup, adaptive query complexity                                                                                                                                | 2-3 days | "When" queries follow timelines, "who/what" queries traverse entity links                               |
| **5. Nightly Maintenance + Causal Inference** | Ebbinghaus decay, pruning, entity enrichment, causal edge inference (LLM)                                                                                                                               | 2-3 days | Memory self-organizes, irrelevant facts fade, "why" queries gain causal graph                           |
| **6. Observability**                          | `memory_stats` with graph metrics (§13), `explain_fact` (provenance chain), weak facts report                                                                                                           | 1-2 days | Memory quality is measurable and debuggable                                                             |


**Phase 1 implementation note:** Deploy the full schema from day one. Early phases only populate a subset of columns (e.g., `event_time = created_at`, `source_type = 'conversation'`). No schema changes needed as features are added.

## Consequences

### Comparison with ADR 005


| Aspect               | ADR 005 (Memory v1)                                    | ADR 008 (Memory v2)                                                    |
| -------------------- | ------------------------------------------------------ | ---------------------------------------------------------------------- |
| Storage model        | Flat `memories` table, `kind` field                    | Graph: `nodes` + `edges` + `entities`                                  |
| Node types           | 4 (episode, fact, preference, reflection)              | 4 cognitive types (episodic, semantic, procedural, opinion)            |
| Relationships        | `source_ids` JSON, `entity_ids` JSON                   | Typed edges: temporal, causal, entity, derived_from, supersedes        |
| Entity storage       | `entities` table + JSON `entity_ids`                   | `entities` table + `node_entities` junction (indexed)                  |
| Retrieval            | FTS5 + vector + RRF (intent-blind)                     | Intent-aware: FTS5 + vector + graph BFS + RRF                          |
| Write path           | Regex NER + nightly consolidation agent (separate ext) | Integrated write-path agent (same ext), regex hot-path + LLM slow-path |
| Satellite extensions | 3 (memory_maintenance, memory_reflection, ner)         | 0 (all absorbed into single memory extension)                          |
| Provenance           | `source_ids` JSON array                                | `derived_from` edges with evidence                                     |
| Knowledge evolution  | `attributes.supersedes` JSON field                     | `supersedes` edges, bi-temporal soft-delete                            |
| Decay                | Ebbinghaus (same)                                      | Ebbinghaus (same)                                                      |
| Protocols            | `ToolProvider` + `ContextProvider`                     | `ToolProvider` + `ContextProvider` + `SchedulerProvider`               |


### Benefits

- **Relationship reasoning** — temporal chains, causal graphs, and entity networks enable queries that were impossible with the flat model.
- **Single extension** — one `memory` directory instead of four. Fewer manifests, fewer dependencies, simpler lifecycle.
- **Agent-powered write path** — replaces hundreds of lines of hardcoded extraction/NER/conflict logic with a prompt and tools. Easier to evolve (change prompt, not code).
- **Intent-aware retrieval** — "why" questions traverse causal edges, "when" questions follow timelines. Quality improvement without LLM cost.
- **Evidence traceability** — every fact has `derived_from` edges to source episodes. The agent can answer "how do you know that?" by traversing the provenance chain.
- **Heartbeat synergy** — graph-based retrieval gives Heartbeat causal chains and temporal context for free via `ContextProvider`.

### Risks and Mitigations


| Risk                                            | Severity | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Write-path agent produces poor fact extractions | Medium   | Confidence scoring + `derived_from` provenance enables correction. Source episodes are immutable — bad extractions can be re-run. Prompt engineering + structured output (Pydantic models) constrain LLM output.                                                                                                                                                                                                                                                         |
| Causal inference produces false edges           | Medium   | LLM inferring cause-effect from episode pairs has high hallucination risk — false `causal` edges degrade "why" queries. Mitigations: causal edges default to `confidence = 0.7` (below user-stated facts at 1.0); causal inference is deferred to Phase 5 (after the base system is validated); the agent prompt requires explicit cause-effect language, not speculation. Temporal edges (deterministic, auto-created) provide 80% of timeline value without this risk. |
| Graph queries become slow at scale              | Low      | Recursive CTEs have depth limits (3-4). Indexes on `relation_type`, `source_id`, `target_id`. For a personal agent, 10K-100K nodes is the realistic ceiling — well within SQLite's comfort zone.                                                                                                                                                                                                                                                                         |
| Entity resolution errors (wrong merges)         | Medium   | Conservative thresholds: exact alias match in hot path, LLM verification in write-path. Entity merges are logged in `attributes`. Manual `correct_fact` and future `split_entity` tools provide recourse.                                                                                                                                                                                                                                                                |
| Write-path agent interrupted mid-consolidation  | Medium   | Idempotency protocol (§10.1): `is_session_consolidated` check before processing; `mark_session_consolidated` only after all operations complete. Partial work (saved nodes) is harmless — duplicate runs may create duplicate facts, caught by deduplication in the next consolidation.                                                                                                                                                                                  |
| No backward compatibility with v1 data          | Low      | Accepted trade-off. Fresh start is preferred over complex migration from a fundamentally different schema.                                                                                                                                                                                                                                                                                                                                                               |


## Relation to Other ADRs

- **ADR 002 (Extensions)** — Memory v2 implements `ToolProvider` + `ContextProvider` + `SchedulerProvider`. Protocol composition replaces the need for satellite extensions.
- **ADR 003 (Agent-as-Extension)** — The write-path memory agent is an internal agent (not an `AgentProvider` visible to the Orchestrator). It uses the same `agents` SDK and `Runner.run()` but is invoked privately by the memory extension. Model resolution goes through `context.model_router.get_model("memory_agent")` — the same `ModelRouter` path that all extensions use (see §10, Model Resolution). The `agent_config` block in the manifest registers the model with the router at startup, ensuring provider switching in `config/settings.yaml` applies to the memory agent without code changes.
- **ADR 004 (Event Bus)** — This ADR introduces `session.completed` as a new kernel-published EventBus topic (core change C1, §15). `MessageRouter` publishes this event when a session rotates due to inactivity. Memory v2 subscribes to it as the primary consolidation trigger. Other extensions can subscribe to the same topic for their own session-boundary logic.
- **ADR 005 (Memory v1)** — **Superseded.** This ADR replaces the flat model with a graph model, absorbs satellite extensions, and adds an agent-powered write path.

## Alternatives Considered

**Keep flat model, add `edges` table alongside `memories`.** Rejected: retrofitting relationships onto a `kind`-based flat table creates hybrid complexity worse than either pure model. A clean break is simpler.

**Use external graph database (Neo4j, DGraph).** Rejected: adds operational dependency, violates the embedded/local-first principle (ADR 002). SQLite with recursive CTEs handles the graph scale of a personal agent (10K-100K nodes).

**Make write-path agent an `AgentProvider` extension.** Rejected: the write-path agent is an internal implementation detail of the memory extension, not a system-visible agent. Exposing it as `AgentProvider` would add it to the Orchestrator's tool list via `Loader.get_agent_tools()` (ADR 003), where it has no business — the Orchestrator should never call "consolidate session" directly. The agent is instead a private `Agent` instance created via `context.model_router.get_model("memory_agent")`, the standard `ModelRouter` mechanism (see §10, Model Resolution). This ensures provider-agnostic model resolution (switching OpenAI → Anthropic → local model in `settings.yaml` automatically propagates to the memory agent) without exposing internal operations to the Orchestrator.

**Use LLM for intent classification on the read path.** Rejected: calling an LLM on every `ContextProvider.get_context()` invocation violates the "LLM on Write, Algorithms on Read" principle (§1). Adds 200-500ms latency and per-query cost. The embedding-based classifier (§11.1) achieves multilingual intent routing at ~2ms by reusing the query embedding already computed for vector search — zero additional model calls.

**Keep `ner` as a separate extension for future reuse.** Rejected: `ner` currently has exactly one consumer (`memory`), and the multi-provider pipeline (regex → spaCy → LLM) adds complexity without proportional value. Regex NER moves inline to the hot path; LLM NER is handled by the write-path agent's prompt. If a future extension needs NER, it can be re-extracted at that point.

**Implement community detection (Label Propagation).** Rejected for now: high implementation complexity, marginal value for a single-user agent with <100K nodes. The schema supports it (entities can be grouped via `attributes`), but the algorithm is not in scope.

**Implement point-in-time graph queries ("knowledge at time T").** Rejected for now: the `valid_from`/`valid_until` fields are populated correctly, but the compound SQL queries required in SQLite are disproportionately complex for a feature that a personal agent rarely needs. Forward-compatible: can be added later without schema changes.

## References

- [Zep/Graphiti](https://arxiv.org/abs/2501.13956) — Temporal knowledge graph for agentic applications. Bi-temporal model, precomputed graph, -90% latency.
- [Mem0](https://memo.d.foundation/breakdown/mem0) — Vector + graph parallel stores, LLM-driven write-path.
- [MAGMA](https://arxiv.org/abs/2601.03236) — 4 orthogonal graphs, adaptive traversal, dual-stream evolution.
- [A-MEM](https://arxiv.org/abs/2502.12110) - A-MEM: Agentic Memory for LLM Agents
- Research concept: "Cognitive Memory System — Technical Implementation Concept" (Feb 2026) — Synthesis of 10+ SOTA systems.
- ADR 002: Nano-Kernel + Extensions
- ADR 003: Agent-as-Extension
- ADR 004: Event Bus in Core
- ADR 005: Simplified Memory System (superseded)

