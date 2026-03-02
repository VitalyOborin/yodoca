# ADR 016: Memory v3 — Hierarchical Knowledge Graph with Atomic Facts

## Status

Proposed. **Supersedes ADR 008.**

## Context

### What exists today

ADR 008 delivered Memory v2: a graph-based system with four node types (`episodic`, `semantic`, `procedural`, `opinion`), five edge types, entity anchors, intent-aware hybrid retrieval (FTS5 + vector + graph BFS + RRF), and an LLM-powered write-path agent for post-session consolidation. This was a major step forward from the flat model of ADR 005.

In continued use, three structural limitations of the v2 architecture have become clear:

| Problem | Detail |
|---------|--------|
| **Full-episode write path** | The write-path agent processes entire episodes as units. On longer sessions, the LLM's "forgetting effect" on long context reduces exhaustivity — it misses facts that are mentioned once in a 30-message conversation. ATOM research shows that **atomic decomposition first, then extraction** yields +31% exhaustivity. |
| **FTS5 on nodes scales linearly** | At 100K+ episodic nodes, FTS5 search degrades linearly. The index covers the entire `content` field of every node, including raw episodes that are rarely useful for retrieval. There is no separation between the non-lossy archive and the semantic index. |
| **Flat semantic model** | All semantic knowledge lives in `nodes.content` — a text blob. There is no structural distinction between the subject, predicate, and object of a fact. Conflict detection via hybrid search is unreliable for semantically similar but logically different facts ("Ivan works at X" vs. "Ivan works at Y"). Without structured triples, graph traversal cannot follow relational paths. |
| **No abstraction hierarchy** | Retrieval always operates at the individual-fact level. There is no mechanism for higher-level summaries of related entities (communities). Context assembly must re-derive structure from flat results every time. |

### SOTA convergence (2025–2026)

Three competing architectures in the literature converge on the same core insight: **facts should be edges (structured triples), not content inside nodes**. This inverts the v2 model where facts are nodes with text content.

**ATOM (Adaptive & Optimized Temporal KG)** — Decomposes each input into atomic facts first, then extracts 5-tuples \((e_s, r_p, e_o, t_{start}, t_{end})\) from each atomic fact in parallel. Merge phase is deterministic (embedding-based, no LLM). Key metrics vs. direct extraction: +31% exhaustivity, +33% stability, −93% latency on the merge step.

**Graphiti/Zep (Bi-Temporal Knowledge Graph)** — The most production-mature architecture. Organizes the graph into three hierarchical subgraphs: Episode (non-lossy raw data), Semantic Entity (entities + fact-edges), and Community (clustered summaries). Bi-temporal model tracks event time \(T\) and ingestion time \(T'\) independently. Benchmark vs. MemGPT: +18.5% accuracy on LongMemEval, −90% latency (3.2s vs. 31.3s), 1.6K tokens context vs. 115K full-context baseline.

**NERD (Entity-Centered Memory)** — Entity-centric organization changes scaling from corpus-linear to query-complexity-constant. Each entity is a Node Entity Relational Document that accumulates all facts, relations, and history. Claims "a 10M token codebase queries at the same cost as a 100K token document."

### Design goals for v3

- **Atomic fact extraction** — decompose episodes into minimal self-contained statements before processing (ATOM pattern).
- **Facts as edges** — structured triples \((subject, predicate, object)\) with dual-time validity, not text blobs in nodes.
- **Three-tier hierarchy** — episodes (non-lossy) → entities + facts (semantic) → communities (summaries).
- **Predominantly deterministic merge** — entity resolution via multi-tier strategy (exact match → alias → cosine similarity → LLM only for ambiguous cases); fact deduplication and temporal conflict resolution are fully deterministic.
- **SQLite-native** — no external graph database; preserve standalone character.
- **Breaking changes allowed** — v2 schema is replaced entirely; migration re-processes episodes through the new pipeline.

## Decision

### 1. Core Principle: Facts Are Edges, Entities Are Nodes

This is the fundamental inversion from v2. In v2, a semantic node stores `content = "Ivan works at Acme Corp"`. In v3, this becomes:

```
Entity("Ivan") --[WORKS_AT, t_valid=2024-01, t_invalid=NULL]--> Entity("Acme Corp")
  fact_text: "Ivan works at Acme Corp as a senior engineer"
```

The entity nodes hold identity and summary; the fact edges hold structured knowledge with temporal validity. This enables:

- **Structural conflict detection** — two facts with the same `(subject, predicate, object_type)` but different objects are a conflict by definition, no fuzzy search needed.
- **Graph traversal along relationships** — BFS from an entity follows typed predicates, not generic "related" edges.
- **Temporal reasoning** — `t_valid` / `t_invalid` on each fact-edge enables "what was true at time T" queries.

### 2. Three-Tier Storage Hierarchy

```
┌─────────────────────────────────────────────────┐
│  TIER 3: Community Subgraph                     │
│  Clusters of related entities + LLM summaries   │
│  Updated incrementally via label propagation    │
├─────────────────────────────────────────────────┤
│  TIER 2: Semantic Entity Subgraph               │
│  Entities (nodes) + Facts (edges, 5-tuples)     │
│  Atomic facts with dual-time timestamps         │
├─────────────────────────────────────────────────┤
│  TIER 1: Episode Subgraph (non-lossy)           │
│  Raw messages, immutable, never deleted          │
│  Linked to entities via episode_entities         │
└─────────────────────────────────────────────────┘
```

**Tier 1 — Episodes** are raw conversation messages. They are write-once, never modified or deleted. They are **not** indexed in FTS5 — only facts are. This eliminates the v2 problem where FTS5 degrades on 100K+ episodic nodes.

**Tier 2 — Entities + Facts** are the core knowledge layer. Entities are canonical identity anchors (like v2). Facts are first-class edge objects with structured subject–predicate–object triples, dual-time validity, provenance to source episodes, and their own embeddings. FTS5 indexes only `fact_text`, keeping the full-text index compact and relevant.

**Tier 3 — Communities** are dynamic clusters of related entities with LLM-generated summaries. They replace v2's absence of any abstraction above individual facts. Communities enable high-level retrieval ("tell me about the Yodoca project ecosystem") without scanning all individual facts.

### 3. Data Schema

Complete replacement of the v2 schema (`nodes`, `edges`, `entities`, `node_entities`).

```sql
-- ================================================================
-- TIER 1: Episodes (non-lossy, immutable)
-- ================================================================
CREATE TABLE IF NOT EXISTS episodes (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    actor       TEXT NOT NULL,        -- 'user' | 'assistant' | extension_id
    session_id  TEXT NOT NULL,
    t_obs       INTEGER NOT NULL,     -- observation timestamp (unix ms)
    created_at  INTEGER NOT NULL      -- ingestion timestamp T'
);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_episodes_t_obs ON episodes(t_obs);

-- ================================================================
-- TIER 2a: Entity Nodes
-- ================================================================
CREATE TABLE IF NOT EXISTS entities (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    aliases       TEXT DEFAULT '[]',  -- JSON: ["Ivan", "иван", "the senior engineer"]
    summary       TEXT,
    entity_type   TEXT,               -- open taxonomy, no CHECK constraint
    embedding     BLOB,               -- embedding dimensions from config
    mention_count INTEGER DEFAULT 1,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

-- ================================================================
-- TIER 2b: Atomic Facts (edges as first-class objects)
-- ================================================================
CREATE TABLE IF NOT EXISTS facts (
    id                TEXT PRIMARY KEY,
    subject_id        TEXT NOT NULL REFERENCES entities(id),
    predicate         TEXT NOT NULL,   -- ALL_CAPS relation type
    object_id         TEXT NOT NULL REFERENCES entities(id),
    fact_text         TEXT NOT NULL,   -- human-readable detail
    embedding         BLOB,
    -- Dual-time model (ATOM + Graphiti)
    t_valid           INTEGER,         -- when fact became true (event time T)
    t_invalid         INTEGER,         -- when fact stopped being true
    t_created         INTEGER NOT NULL, -- when ingested (T')
    t_expired         INTEGER,         -- when superseded in system (T')
    -- Provenance
    source_episode_id TEXT REFERENCES episodes(id),
    confidence        REAL DEFAULT 1.0,
    invalidated_by    TEXT REFERENCES facts(id)
);
CREATE INDEX IF NOT EXISTS idx_facts_subject
    ON facts(subject_id) WHERE t_expired IS NULL;
CREATE INDEX IF NOT EXISTS idx_facts_object
    ON facts(object_id) WHERE t_expired IS NULL;
CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate);

-- FTS5 on fact_text only (not episodes — keeps index compact)
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact_text,
    content='facts',
    content_rowid=rowid,
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS facts_fts_insert AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, fact_text) VALUES (new.rowid, new.fact_text);
END;
CREATE TRIGGER IF NOT EXISTS facts_fts_update AFTER UPDATE OF fact_text ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, fact_text)
        VALUES ('delete', old.rowid, old.fact_text);
    INSERT INTO facts_fts(rowid, fact_text) VALUES (new.rowid, new.fact_text);
END;
CREATE TRIGGER IF NOT EXISTS facts_fts_delete AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, fact_text)
        VALUES ('delete', old.rowid, old.fact_text);
END;

-- ================================================================
-- TIER 3: Communities (dynamic clusters)
-- ================================================================
CREATE TABLE IF NOT EXISTS communities (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,       -- key terms for cosine search
    summary       TEXT NOT NULL,
    embedding     BLOB,
    member_count  INTEGER DEFAULT 0,
    updated_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS community_members (
    community_id  TEXT REFERENCES communities(id),
    entity_id     TEXT REFERENCES entities(id),
    PRIMARY KEY (community_id, entity_id)
);

-- ================================================================
-- Episode → Entity links (bidirectional index)
-- ================================================================
CREATE TABLE IF NOT EXISTS episode_entities (
    episode_id  TEXT REFERENCES episodes(id),
    entity_id   TEXT REFERENCES entities(id),
    PRIMARY KEY (episode_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_ee_entity ON episode_entities(entity_id);

-- ================================================================
-- Vector search (sqlite-vec)
-- ================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS vec_entities USING vec0(
    entity_id TEXT PRIMARY KEY,
    embedding float[256]
);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_facts USING vec0(
    fact_id TEXT PRIMARY KEY,
    embedding float[256]
);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_communities USING vec0(
    community_id TEXT PRIMARY KEY,
    embedding float[256]
);

-- ================================================================
-- Fact access tracking (for Ebbinghaus decay)
-- ================================================================
CREATE TABLE IF NOT EXISTS fact_access_log (
    fact_id     TEXT NOT NULL REFERENCES facts(id),
    accessed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fal_fact ON fact_access_log(fact_id);

-- ================================================================
-- Pipeline intermediate state (retry semantics)
-- ================================================================
CREATE TABLE IF NOT EXISTS pipeline_queue (
    id          TEXT PRIMARY KEY,
    episode_id  TEXT NOT NULL REFERENCES episodes(id),
    atomic_fact TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','processing','done','failed')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pq_status ON pipeline_queue(status)
    WHERE status IN ('pending','failed');

-- ================================================================
-- Maintenance / consolidation tracking
-- ================================================================
CREATE TABLE IF NOT EXISTS maintenance_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions_consolidations (
    session_id       TEXT PRIMARY KEY,
    first_seen_at    INTEGER NOT NULL,
    consolidated_at  INTEGER
);
```

#### Schema design rationale

**Why `facts` as a separate table with `subject_id` / `object_id` instead of `edges` between `nodes`:** In v2, facts are nodes with a text `content` field. Edges only express structural relationships (`temporal`, `causal`, `supersedes`, etc.). In v3, facts **are** the relationships — structured triples where the subject and object are entities. This is the fundamental model inversion. A separate `facts` table (rather than reusing `edges`) makes the triple structure explicit and indexable: filtered indexes on `subject_id WHERE t_expired IS NULL` enable efficient "all active facts about entity X" queries.

**Why `episodes` replaces episodic nodes:** Episodes in v2 were stored in the same `nodes` table as semantic facts, sharing columns like `confidence`, `decay_rate`, `access_count` that never apply to episodes. Tier 1 episodes are structurally different — immutable, no decay, no embeddings, no confidence — so they deserve their own table with a minimal schema.

**Why dual-time on facts:** `t_valid` / `t_invalid` track the real-world validity period of a fact (event time). `t_created` / `t_expired` track when the fact entered/left the system's current knowledge (ingestion time). This distinction (from ATOM and Graphiti) is essential for correctly handling "Ivan no longer works at X" — the fact `WORKS_AT` gets `t_invalid = now` rather than being deleted, preserving the historical record. The `t_expired` field handles supersession: when a new contradicting fact is ingested, the old fact's `t_expired` is set, and `invalidated_by` points to the replacement.

**Why no node types (episodic/semantic/procedural/opinion):** The v2 taxonomy becomes unnecessary. Episodes are in their own table. Semantic facts are structured triples in `facts`. Procedural knowledge ("to deploy X, do Y") is a fact where the predicate is a verb phrase and the subject/object encode the procedure. Opinions are facts with lower `confidence` or specific predicates like `PREFERS`, `DISLIKES`. The type is implicit in the predicate, not a column constraint.

**Why `aliases` on entities:** Entity resolution must handle "Ivan Petrov", "Ivan", "иван", "the senior engineer Ivan" as the same entity. The `aliases` JSON array accumulates all known mention forms. Once a new mention is resolved (via embedding similarity or LLM disambiguation), it is added to aliases so that future occurrences resolve instantly via the Tier 2 alias match (see §4.2). This is the same pattern Graphiti uses to amortize the cost of LLM-assisted disambiguation.

**Why open taxonomy for `entity_type`:** V2's CHECK constraint (`person`, `project`, `organization`, `place`, `concept`, `tool`) is too rigid. New entity types emerge naturally from conversation (e.g. `event`, `document`, `skill`). An open TEXT field lets the write-path agent assign types freely. Community clustering handles grouping.

**Why `invalidated_by` on facts:** Enables supersession chains: fact A is invalidated by fact B, which is invalidated by fact C. Traversing this chain shows knowledge evolution for any entity-pair relationship. This replaces v2's `supersedes` edge type.

### 4. Write-Path: Atomic Fact Extraction Pipeline

The v2 write-path processes entire sessions through a single LLM call. The v3 pipeline decomposes input into atomic facts first, then processes each one independently. This is the ATOM pattern.

```
Episode arrives (user_message | agent_response)
  │
  ├─ 1. Store episode in Tier 1 (immutable, <5ms)
  │
  └─ 2. Queue for atomic processing (post-session or real-time)
         │
         ├─ Module 1: Atomic Decomposition (LLM)
         │    "Decompose into minimal self-contained facts"
         │    Input: episode text → Output: 3-8 atomic fact strings
         │
         ├─ Modules 2+3: Per atomic fact, in parallel (LLM)
         │    ├─ Extract entities (subject, object)
         │    ├─ Extract fact tuple (subject, predicate, object)
         │    └─ Extract temporal bounds (t_valid, t_invalid)
         │
         └─ Module 4: Merge & Persist (NO LLM — deterministic)
              ├─ Entity resolution (cosine similarity + exact name)
              ├─ Fact deduplication (same entity pair + similar text)
              └─ Temporal resolution (invalidate conflicting facts)
```

The critical architectural change: **Module 4 is entirely deterministic**. Entity resolution uses embedding cosine similarity with a configurable threshold plus exact name matching. Fact deduplication is constrained to the same entity pair (as in Graphiti). Temporal conflict resolution compares predicates on the same entity pair and sets `t_expired` on the older fact. No LLM calls in the merge step — this is what gives ATOM its −93% latency on merge.

#### 4.1 Atomic Decomposition with Durable Queue

The pipeline persists intermediate state to enable retry on partial failures. Module 1 (decomposition) writes atomic facts to `pipeline_queue` before Module 2+3 processing begins. If the process crashes mid-pipeline, pending queue items are retried on the next consolidation run.

```python
class AtomicWritePipeline:
    MAX_ATTEMPTS = 3
    
    async def process_episode(self, episode: Episode) -> None:
        # Module 1: Decompose and persist to durable queue
        atomic_texts = await self._decompose_to_atomic_facts(episode)
        queue_ids = await self._enqueue_atomic_facts(episode.id, atomic_texts)
        
        # Modules 2+3: Process each queued item in parallel
        results = await self._process_queue_items(queue_ids, episode)
        
        # Module 4: Merge deterministically
        await self._merge_and_persist(results, episode.id)
    
    async def _enqueue_atomic_facts(
        self, episode_id: str, texts: list[str]
    ) -> list[str]:
        """Persist atomic facts to pipeline_queue with status='pending'.
        If process crashes after this point, retry picks up from here."""
        ...
    
    async def _process_queue_items(
        self, queue_ids: list[str], episode: Episode
    ) -> list[AtomicFactResult]:
        """Process each queue item. On failure: increment attempts,
        record error, set status='failed'. Items with attempts >= MAX_ATTEMPTS
        are skipped and logged for manual review."""
        ...
    
    async def retry_failed(self) -> int:
        """Called by nightly maintenance. Re-processes items with
        status='pending' or status='failed' where attempts < MAX_ATTEMPTS.
        Returns count of successfully processed items."""
        ...
    
    async def _decompose_to_atomic_facts(self, episode: Episode) -> list[str]:
        """LLM call: decompose episode text into atomic statements.
        Each statement must be independently understandable.
        Typical output: 3-8 atomic facts per episode."""
        ...
    
    async def _process_atomic_fact(
        self, atomic_fact: str, episode: Episode
    ) -> AtomicFactResult:
        entities, fact_tuple, temporal = await asyncio.gather(
            self._extract_entities(atomic_fact, episode.t_obs),
            self._extract_fact_tuple(atomic_fact),
            self._extract_temporal(atomic_fact, episode.t_obs)
        )
        return AtomicFactResult(entities, fact_tuple, temporal)
```

**Retry semantics:** If Module 2+3 fails for a specific atomic fact (LLM timeout, malformed output), only that queue item is marked `failed`. Other atomic facts from the same episode continue processing. The nightly maintenance task calls `retry_failed()` to re-attempt items with `attempts < 3`. Items that exhaust retries remain in the queue with `status='failed'` for diagnostic purposes — the source episode is never lost (Tier 1 is immutable), so manual re-processing is always possible.

#### 4.2 Entity Resolution (Module 4, Step 1)

Entity resolution is the most critical deterministic operation in the merge step. A multi-tier strategy handles the range from trivial matches ("Ivan" → known entity "Ivan Petrov") to ambiguous references ("the senior engineer Ivan"):

```python
class EntityResolver:
    async def resolve(self, name: str, context_embedding: list[float]) -> Entity | None:
        normalized = name.strip().lower()
        
        # Tier 1: Exact match on normalized name — O(1)
        exact = await self.db.get_entity_by_normalized_name(normalized)
        if exact:
            return exact
        
        # Tier 2: Alias match — O(aliases) via JSON scan
        alias_match = await self.db.get_entity_by_alias(normalized)
        if alias_match:
            return alias_match
        
        # Tier 3: Embedding similarity — O(log N) via sqlite-vec
        candidates = await self.db.vec_search_entities(context_embedding, top_k=5)
        high_confidence = [c for c in candidates if c.score > self.threshold_high]
        if len(high_confidence) == 1:
            await self._add_alias(high_confidence[0].entity, name)
            return high_confidence[0].entity
        
        # Tier 4: LLM disambiguation — only for ambiguous zone
        ambiguous = [c for c in candidates if c.score > self.threshold_low]
        if ambiguous:
            return await self._llm_disambiguate(name, ambiguous)
        
        return None  # Create new entity
```

**Configuration thresholds:**

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `entity_resolution_threshold` | 0.85 | High-confidence cosine match (Tier 3) |
| `entity_resolution_ambiguous_threshold` | 0.70 | Lower bound for LLM disambiguation (Tier 4) |

**Alias accumulation:** When Tier 3 or Tier 4 resolves a new mention to an existing entity, the mention text is added to `entities.aliases` (JSON array). This means "Ivan Petrov", "Ivan", "иван", "the senior engineer Ivan" all accumulate as aliases, making future Tier 2 matches instant. Graphiti uses the same approach — LLM-assisted resolution is expensive but runs once per unique mention form; subsequent occurrences hit the alias cache.

**Design rationale for Tier 4 (LLM in merge):** The original ATOM paper uses purely embedding-based resolution. However, Graphiti demonstrates that LLM disambiguation for the ambiguous zone (0.70–0.85 cosine) significantly improves resolution quality for multilingual and context-dependent mentions. The LLM call is scoped narrowly — it receives only the mention, the candidate entities with summaries, and recent context — not the full graph. In practice, this triggers for <5% of mentions (most resolve at Tiers 1–3), keeping the merge step predominantly deterministic.

#### 4.3 Deterministic Merge (Module 4, Steps 2–3)

```python
async def _merge_and_persist(
    self, results: list[AtomicFactResult], episode_id: str
) -> None:
    """
    Step 1: Entity resolution (see §4.2 — multi-tier with LLM fallback)
    Step 2: Fact deduplication — constrained to same entity pair
    Step 3: Temporal conflict resolution — invalidate contradicting facts
    """
    async with self.db.transaction():
        for result in results:
            resolved = await self._resolve_entities(result.entities)
            await self._upsert_fact(
                result.fact_tuple, resolved,
                result.temporal, episode_id
            )
```

Fact deduplication compares `fact_text` embeddings within the same `(subject_id, object_id)` pair. If cosine similarity exceeds `fact_dedup_threshold` (default 0.90), the existing fact is kept and the new one is discarded. Temporal conflict resolution: if a new fact has the same `(subject_id, predicate)` but a different `object_id`, the old fact gets `t_expired = now` and `invalidated_by = new_fact.id`. Both steps are deterministic — no LLM.

#### 4.4 Predicate Canonicalization

The `facts.predicate` field uses an open ALL_CAPS taxonomy. Without normalization, the same relationship drifts into multiple forms: `WORKS_AT`, `EMPLOYED_BY`, `IS_EMPLOYEE_OF`. This fragments the graph — structural conflict detection on `(subject, predicate)` fails if two facts about the same relationship use different predicates.

**Strategy: preferred predicates list + extraction prompt.** The manifest config defines a `preferred_predicates` list (see §14). The entity+fact extraction prompt (Module 2+3) instructs the LLM to use predicates from this list when applicable, and only invent a new predicate when none fits:

```
Extract the relationship predicate. Use one of the preferred predicates
if applicable: WORKS_AT, LIVES_IN, MANAGES, CREATED, USES_TECH, PART_OF,
KNOWS, PREFERS, DISLIKES, HAPPENED_AT, CAUSED, FOLLOWED_BY.
If none fits, create a new ALL_CAPS predicate.
```

This is the approach Graphiti uses — a canonicalization hint in the extraction prompt, not a hard constraint. New predicates emerge naturally (e.g. `GRADUATED_FROM`, `INVESTED_IN`), but common relationships converge on a consistent vocabulary.

**Post-hoc normalization (Module 4, deterministic):** During merge, if a new fact's predicate is not in the preferred list, a lightweight check compares its embedding against the preferred predicates' embeddings. If cosine similarity exceeds 0.90, the preferred predicate is substituted. This catches LLM inconsistencies without an extra LLM call.

#### 4.5 Model Router Integration

Different pipeline stages have different model requirements:

| Stage | Model requirement | Rationale |
|-------|------------------|-----------|
| Atomic decomposition | Cheap, fast (e.g. `gpt-4.1-mini`) | High-volume, low-complexity |
| Entity + fact extraction | Cheap, fast | Structured output from atomic text |
| Community summary | Mid-tier (e.g. `gpt-4.1-mini`) | Needs synthesis across multiple facts |
| Module 4 (merge) | No LLM | Deterministic algorithms only |

The memory extension uses `context.model_router.get_model("memory_agent")` as in v2. The pipeline internally dispatches different prompt complexities to the same model — the model tier is chosen in config to balance cost and quality for the predominant use (atomic decomposition).

### 5. Read-Path: Three-Tier Hierarchical Retrieval

Retrieval replaces v2's flat search with a three-tier parallel strategy. The "LLM on Write, Algorithms on Read" principle from v2 is preserved — no LLM calls on the read path.

```python
class HierarchicalRetriever:
    async def search(self, query: str, **kwargs) -> RetrievalResult:
        query_embedding = await self.embedder.embed(query)
        intent = self._intent_classifier.classify(query, query_embedding)
        
        facts, entities, communities = await asyncio.gather(
            self._search_facts(query, query_embedding, intent),
            self._search_entities(query, query_embedding),
            self._search_communities(query, query_embedding),
        )
        
        expanded_facts = await self._bfs_expand(entities, depth=2)
        
        return self._rrf_fuse(facts, expanded_facts, communities)
```

#### 5.1 Fact Search (Tier 2)

Hybrid search on `facts`: cosine similarity on `facts.embedding` + FTS5 on `facts_fts`. Only active facts (`t_expired IS NULL`) are searched. This is equivalent to v2's hybrid search but scoped to structured facts instead of all node types.

#### 5.2 BFS Graph Expansion

From entities found in Tier 2, expand outward along fact-edges to discover related knowledge:

```sql
WITH RECURSIVE graph_bfs(entity_id, depth) AS (
    SELECT id, 0 FROM entities WHERE id IN (?)
    UNION ALL
    SELECT
        CASE WHEN f.subject_id = g.entity_id
             THEN f.object_id
             ELSE f.subject_id END,
        g.depth + 1
    FROM facts f
    JOIN graph_bfs g ON (f.subject_id = g.entity_id
                      OR f.object_id = g.entity_id)
    WHERE g.depth < ? AND f.t_expired IS NULL
)
SELECT DISTINCT f.* FROM facts f
JOIN graph_bfs g ON (f.subject_id = g.entity_id OR f.object_id = g.entity_id)
WHERE f.t_expired IS NULL
ORDER BY f.confidence DESC
LIMIT ?  -- bfs_max_facts (default 50)
```

This recursive CTE traverses the entity–fact graph up to a configurable depth. Because facts are edges between entities, BFS naturally follows relational paths: `Ivan → WORKS_AT → Acme → HAS_PROJECT → Phoenix → USES_TECH → Rust`.

**Fan-out protection:** Dense entities (e.g. a concept like "Python" with 100+ connected facts) can cause BFS at depth 2 to return thousands of results. The `LIMIT` clause caps results at `bfs_max_facts` (default 50, configurable), ordered by confidence to prioritize the most reliable facts. The recursive CTE itself does not deduplicate `entity_id` across depths — the `DISTINCT` on the outer query handles this. The depth limit and fact cap together bound the worst case to a predictable result size.

#### 5.3 Community Search (Tier 3)

For broad queries ("tell me about the Yodoca project"), community summaries provide a pre-computed overview without scanning individual facts. Cosine similarity on community embeddings returns the most relevant clusters, whose summaries are included in the context.

#### 5.4 Timeline Retrieval

The `get_timeline` Orchestrator tool requires a dedicated retriever path — it is not an RRF-fused search but a direct temporal query on the fact graph:

```python
async def get_timeline(
    self, entity_id: str | None = None,
    after: int | None = None, before: int | None = None,
    limit: int = 50,
) -> list[Fact]:
    """Chronological facts ordered by t_valid.
    Optionally filtered by entity and time range.
    No RRF fusion — direct temporal ordering."""
    return await self.db.execute("""
        SELECT f.* FROM facts f
        WHERE f.t_expired IS NULL
          AND (:entity_id IS NULL
               OR f.subject_id = :entity_id
               OR f.object_id = :entity_id)
          AND (:after IS NULL OR f.t_valid >= :after)
          AND (:before IS NULL OR f.t_valid <= :before)
        ORDER BY f.t_valid ASC
        LIMIT :limit
    """, {"entity_id": entity_id, "after": after,
          "before": before, "limit": limit})
```

This is intentionally separate from `search()` — timeline queries need deterministic temporal ordering, not relevance ranking. The `when` intent in `search()` still uses RRF fusion with temporal weighting for natural-language queries; `get_timeline` serves the explicit tool.

#### 5.5 Intent-Aware Routing

The v2 intent classification system (`EmbeddingIntentClassifier` with exemplars, `KeywordIntentClassifier` fallback) is preserved. Intent now affects which tiers are weighted more heavily in RRF fusion:

| Intent | Tier weighting |
|--------|---------------|
| `who` / `what` | Entity search boosted; BFS expansion deeper |
| `when` | Facts filtered by temporal bounds; episode timeline fallback |
| `why` | BFS expansion along causal predicates |
| `general` | All tiers equally weighted |

#### 5.6 Context Assembly

Replaces v2's four-section assembly with a three-tier structure:

| Section | Budget share | Source |
|---------|-------------|--------|
| **Facts** | 50% | Top-ranked atomic facts (structured triples + `fact_text`) |
| **Entity profiles** | 25% | Entity summaries for mentioned entities |
| **Community context** | 25% | Relevant community summaries |

Each fact is rendered as a structured line: `[Subject] --[PREDICATE]--> [Object]: fact_text (confidence, t_valid)`. This gives the LLM both the triple structure and the natural language detail.

### 6. Community Detection (Tier 3)

Instead of v2's static node types, v3 uses dynamic community clustering via incremental label propagation (Graphiti approach).

**Neighbor definition:** Two entities are "neighbors" for community purposes if they share at least `community_min_shared_facts` active facts (default 2). A single shared fact (e.g. `Python -[USES_TECH]-> Yodoca`) is insufficient — it connects almost everything to common concepts. Requiring 2+ shared facts ensures community membership reflects meaningful relationship density, not incidental co-occurrence.

**Incremental update:** When a new entity is added, it is assigned to the plurality community of its neighbors. If no neighbors have communities, a new community is created. The community summary is regenerated with an LLM call.

**Periodic refresh:** A scheduled task runs full label propagation to correct drift. This is infrequent (weekly or on-demand), not nightly.

```python
class CommunityManager:
    async def on_entity_added(self, entity: Entity) -> None:
        """O(neighbors), not O(graph)."""
        neighbors = await self.get_neighboring_communities(
            entity, min_shared_facts=self.config.community_min_shared_facts
        )
        if not neighbors:
            await self.create_new_community(entity)
            return
        community_id = Counter(neighbors).most_common(1)[0][0]
        await self.add_to_community(entity.id, community_id)
        await self.update_community_summary(community_id)
    
    async def get_neighboring_communities(
        self, entity: Entity, min_shared_facts: int = 2
    ) -> list[str]:
        """Return community IDs of entities that share >= min_shared_facts
        active facts with the given entity."""
        return await self.db.execute("""
            SELECT cm.community_id
            FROM facts f1
            JOIN facts f2 ON (
                (f1.object_id = f2.subject_id OR f1.object_id = f2.object_id
                 OR f1.subject_id = f2.subject_id OR f1.subject_id = f2.object_id)
            )
            JOIN community_members cm ON cm.entity_id = 
                CASE WHEN f1.subject_id = :eid THEN f1.object_id
                     ELSE f1.subject_id END
            WHERE (f1.subject_id = :eid OR f1.object_id = :eid)
              AND f1.t_expired IS NULL
            GROUP BY cm.community_id
            HAVING COUNT(DISTINCT f1.id) >= :min_facts
        """, {"eid": entity.id, "min_facts": min_shared_facts})
    
    async def periodic_refresh(self) -> None:
        """Full label propagation. Scheduled weekly."""
        ...
```

### 7. Orchestrator Tools

The tool set evolves from v2's 6 core tools to reflect the new architecture:

| Tool | Description | Change from v2 |
|------|-------------|----------------|
| `search_memory` | Three-tier hierarchical search with RRF fusion | Replaces flat hybrid search |
| `remember_fact` | Store a structured fact (subject, predicate, object) with entity resolution | Now creates entity nodes + fact edge |
| `correct_fact` | Supersede an existing fact (sets `t_expired`, creates replacement) | Uses `invalidated_by` chain instead of `supersedes` edge |
| `confirm_fact` | Protect fact from expiry (`confidence = 1.0`) | Unchanged semantics |
| `get_entity_info` | Entity profile: summary, all active facts, community membership | Richer output via structured facts |
| `memory_stats` | Graph metrics: entity count, fact count, community count, active/expired ratios | Adapted to new schema |
| `get_timeline` | Chronological facts for an entity or time range | New: uses `t_valid` ordering on fact-edges |
| `forget_fact` | Expire a fact by search match | Uses `t_expired` instead of `valid_until` on nodes |

Tools that are removed: `explain_fact` (provenance is directly available via `source_episode_id` on every fact), `weak_facts` (subsumed by `search_memory` with confidence filter).

All tools continue to return structured Pydantic models or fixed-shape dicts per the agent tools contract (ADR 003, development rules).

### 8. ContextProvider Integration

The `ContextProvider` interface is unchanged. Internally, `get_context()` calls the new `HierarchicalRetriever` instead of the v2 retriever:

```python
async def get_context(self, prompt: str, *, agent_id: str | None = None) -> str | None:
    complexity = classify_query_complexity(prompt)
    params = get_adaptive_params(complexity)
    results = await self._retriever.search(
        query=prompt,
        limit=params['limit'],
        token_budget=params['token_budget'],
    )
    if not results:
        return None
    return self._retriever.assemble_context(results, token_budget=params['token_budget'])
```

`context_priority` remains 50. The Heartbeat and other `ContextProvider` consumers are unaffected by the internal change.

#### 8.1 Current-Session Context (Real-Time vs Post-Session)

The atomic pipeline is **post-session** — it runs after session completion or during nightly maintenance. This means facts extracted from the current conversation are not yet in the entity-fact graph during that same session. Without mitigation, the agent would "forget" something the user said 5 minutes ago if it was in the same session.

**Solution: dual-source context assembly.** The `ContextProvider` injects two layers:

1. **Long-term memory** (Tier 2+3) — from the `HierarchicalRetriever`, covering all previously consolidated knowledge.
2. **Current-session episodes** (Tier 1) — raw episodes from the active `session_id`, fetched directly from the `episodes` table and appended as a `## Recent conversation` section.

```python
async def get_context(self, prompt: str, *, agent_id: str | None = None) -> str | None:
    complexity = classify_query_complexity(prompt)
    params = get_adaptive_params(complexity)
    
    # Long-term: entity-fact graph (post-session consolidated)
    results = await self._retriever.search(
        query=prompt,
        limit=params['limit'],
        token_budget=int(params['token_budget'] * 0.7),
    )
    
    # Current session: raw episodes (not yet in the graph)
    session_episodes = await self._storage.get_recent_session_episodes(
        self._current_session_id,
        limit=params['session_episode_limit'],
    )
    
    context_parts = []
    if results:
        context_parts.append(
            self._retriever.assemble_context(results, token_budget=int(params['token_budget'] * 0.7))
        )
    if session_episodes:
        context_parts.append(self._format_session_episodes(session_episodes))
    
    return "\n\n".join(context_parts) if context_parts else None
```

This is the same pattern v2 uses (the Agents SDK's `SQLiteSession` holds the current conversation, but `ContextProvider` supplements it with recent episodes for cross-turn continuity). The token budget is split 70/30 between long-term memory and session context — the split is configurable.

**Why not real-time pipeline execution?** Running the atomic pipeline on every incoming message would add 1–3 seconds of latency (LLM calls for decomposition + extraction). For a conversational agent, this is unacceptable on the hot path. The design explicitly keeps the hot path at <5ms (episode storage only) and defers structured extraction to post-session. The current-session episode injection bridges the gap.

### 9. Write-Path Agent Tools (Internal)

These tools are private to the memory extension's write-path pipeline — not exposed to the Orchestrator.

| Tool | Description | Change from v2 |
|------|-------------|----------------|
| `get_session_episodes` | Fetch episodes for a session | Now reads from `episodes` table |
| `decompose_to_atomic_facts` | LLM: split episode into atomic statements | **New** — core of the ATOM pattern |
| `extract_entities_and_fact` | LLM: extract (subject, predicate, object) + entities from an atomic fact | Replaces `save_nodes_batch` + `extract_and_link_entities` |
| `resolve_and_persist` | Deterministic: entity resolution + fact upsert + temporal conflict handling | Replaces LLM-based `detect_conflicts` + `resolve_conflict` |
| `update_community` | Incremental community assignment + summary regeneration | **New** — Tier 3 |
| `is_session_consolidated` | Idempotency guard | Unchanged |
| `mark_session_consolidated` | Commit consolidation | Unchanged |
| `update_entity_summary` | Regenerate entity summary and re-embed | Unchanged |

### 10. Nightly Maintenance

The `SchedulerProvider` task `run_nightly_maintenance` is preserved with adapted operations:

1. **Consolidate pending sessions** — same trigger mechanism (§15 in ADR 008), now uses the atomic pipeline.
2. **Retry failed pipeline items** — calls `AtomicWritePipeline.retry_failed()` to re-process items in `pipeline_queue` with `status='pending'` or `status='failed'` where `attempts < pipeline_max_attempts`.
3. **Expire low-confidence facts** — replaces Ebbinghaus decay on nodes. Facts below `confidence_threshold` get `t_expired = now`. The decay formula is the same but applied to `facts.confidence` instead of `nodes.confidence`.
4. **Entity enrichment** — same as v2: regenerate summaries for entities with high `mention_count` but sparse summaries.
5. **Compact fact access log** — deduplicate `fact_access_log` to keep only the most recent access per fact.
6. **Community refresh** — **new**: periodic full label propagation on the entity-fact graph (runs weekly via separate schedule, but can also be triggered here).

Episodic nodes (Tier 1) never expire — they are the non-lossy archive.

### 11. Decay Model

The Ebbinghaus decay model from v2 is preserved but applied to facts instead of nodes:

```
confidence(t) = confidence₀ × exp(-λ × (t - t_last_accessed)^0.8)
```

Facts are the unit of decay. An accessed fact (returned in retrieval results) has its access recorded, resetting the decay clock. Facts confirmed by the user (`confirm_fact`) have `λ = 0` — they never decay.

There is no `decay_rate` or `last_accessed` column on `facts`. Instead, the default decay rate λ comes from configuration (`decay_lambda`, default 0.1). Only user-confirmed facts are exempt (tracked via `confidence = 1.0` after `confirm_fact`). This simplifies the schema — in v2, per-node `decay_rate` was rarely varied.

**Access tracking via `fact_access_log`:** The `facts` table is write-optimized — each retrieval should not require an UPDATE on it. Instead, access events are appended to the separate `fact_access_log` table (insert-only, no updates). The decay service reads `MAX(accessed_at)` per fact during the nightly run. This design avoids write contention on the main `facts` table during high-frequency retrievals.

```sql
-- Nightly decay: compute last access and apply formula
UPDATE facts SET confidence = confidence * exp(
    -:lambda * power((:now - COALESCE(
        (SELECT MAX(accessed_at) FROM fact_access_log WHERE fact_id = facts.id),
        facts.t_created
    )) / 86400000.0, 0.8)
)
WHERE t_expired IS NULL AND confidence > :threshold;

-- Expire facts below threshold
UPDATE facts SET t_expired = :now
WHERE t_expired IS NULL AND confidence < :threshold;
```

The `fact_access_log` can be periodically compacted (keep only the most recent access per fact) during nightly maintenance to prevent unbounded growth.

### 12. Graceful Degradation

Each capability layer degrades independently, following v2's pattern:

| Component | If unavailable | Fallback |
|-----------|---------------|----------|
| `embedding` extension | No vector search | FTS5 keyword search on `facts_fts`. Intent classifier falls back to `KeywordIntentClassifier`. |
| `sqlite-vec` | No ANN index | FTS5 + entity name lookup. Embeddings stored in BLOB but not indexed. |
| LLM (write-path) | No atomic decomposition | Hot path still stores episodes. Consolidation deferred until LLM available. |
| Community layer | No Tier 3 summaries | Retrieval falls back to Tier 1 + Tier 2 only. No functional loss for targeted queries. |

### 13. Migration from Memory v2

Breaking change. No backward compatibility with v2 data schema.

**Migration tool** re-processes all v2 episodes through the new atomic pipeline:

```python
class MemoryV3Migration:
    """Run once. Re-processes all episodes through the atomic pipeline."""
    
    async def migrate(self, v2_db_path: str, v3_db_path: str) -> MigrationReport:
        v2_episodes = await self._load_v2_episodic_nodes(v2_db_path)
        
        report = MigrationReport()
        for episode in v2_episodes:
            try:
                v3_episode = Episode(
                    id=episode.id,
                    content=episode.content,
                    actor=episode.source_role or 'user',
                    session_id=episode.session_id,
                    t_obs=episode.event_time,
                    created_at=episode.created_at,
                )
                await self.pipeline.process_episode(v3_episode)
                report.success += 1
            except Exception as e:
                report.failed.append((episode.id, str(e)))
        
        return report
```

The migration preserves raw episode content (Tier 1 is non-lossy) and re-derives the entire entity-fact-community graph. V2 semantic/procedural/opinion nodes are discarded — they will be re-extracted from episodes by the atomic pipeline.

**Alternative: clean start.** For deployments where re-processing is impractical or v2 data is small, delete the v2 database and start fresh. The agent accumulates knowledge from the first conversation, as with v2.

### 14. Manifest Changes

```yaml
id: memory
name: Memory v3
version: "3.0.0"

entrypoint: main:MemoryExtension

description: |
  Hierarchical knowledge graph with atomic fact extraction.
  Three-tier storage: episodes (non-lossy) → entities + facts (structured triples)
  → communities (clustered summaries). Deterministic entity resolution and
  conflict detection. Intent-aware three-tier retrieval with RRF fusion.

depends_on:
  - embedding

config:
  embedding_dimensions: 256
  # Entity resolution
  entity_resolution_threshold: 0.85
  entity_resolution_ambiguous_threshold: 0.70
  # Fact dedup and decay
  fact_dedup_threshold: 0.90
  confidence_threshold: 0.05
  decay_lambda: 0.1
  # Retrieval
  context_token_budget: 2000
  rrf_k: 60
  rrf_weight_vector: 1.0
  rrf_weight_fts: 1.0
  rrf_weight_graph: 1.0
  intent_similarity_threshold: 0.45
  bfs_max_depth: 2
  bfs_max_facts: 50
  # Community
  community_min_shared_facts: 2
  community_refresh_interval_days: 7
  # Predicate canonicalization
  preferred_predicates:
    - WORKS_AT
    - LIVES_IN
    - MANAGES
    - CREATED
    - USES_TECH
    - PART_OF
    - KNOWS
    - PREFERS
    - DISLIKES
    - HAPPENED_AT
    - CAUSED
    - FOLLOWED_BY
  # Pipeline
  pipeline_max_attempts: 3

agent_config:
  memory_agent:
    model: gpt-4.1-mini

schedules:
  - name: nightly_maintenance
    cron: "0 3 * * *"
    task: run_nightly_maintenance
    description: "Consolidate pending sessions, expire low-confidence facts, enrich entities"
  - name: community_refresh
    cron: "0 4 * * 0"
    task: run_community_refresh
    description: "Full label propagation on entity-fact graph (weekly)"

enabled: true
```

### 15. Extension File Structure

```
sandbox/extensions/memory/
├── manifest.yaml          — extension metadata, schedules, agent config
├── main.py                — MemoryExtension: lifecycle, event handlers, hot path
├── schema.sql             — full three-tier schema
├── storage.py             — MemoryStorage: CRUD, graph operations, write queue
├── pipeline.py            — AtomicWritePipeline: decomposition, extraction, merge
├── retrieval.py           — HierarchicalRetriever: three-tier search, RRF, context assembly
├── community.py           — CommunityManager: label propagation, summary generation
├── decay.py               — Ebbinghaus decay on facts (pure algorithm)
├── tools.py               — Orchestrator tools
├── agent_tools.py         — Write-path agent tools (internal)
├── migration.py           — V2 → V3 migration tool
└── prompts/
    ├── decompose.jinja2   — Atomic decomposition prompt
    ├── extract.jinja2     — Entity + fact extraction prompt
    └── community.jinja2   — Community summary generation prompt
```

Changes from v2: `agent.py` → split into `pipeline.py` (atomic pipeline) + `community.py`. `prompt.jinja2` → split into three purpose-specific prompts in `prompts/`. Added `migration.py`.

## Implementation Phases

| Phase | Scope | Duration | Outcome |
|-------|-------|----------|---------|
| **1. Schema + Episode Tier** | New schema, episode storage, hot path (store episodes only), session consolidation tracking. Migrate `ContextProvider` to pass-through mode. | 2-3 days | Episodes stored in Tier 1. Old tools temporarily disabled. |
| **2. Atomic Pipeline (Tier 2)** | `AtomicWritePipeline`: decomposition, entity extraction, fact extraction, deterministic merge. Entity resolution. `facts_fts` indexing. | 3-4 days | Facts extracted from episodes as structured triples. |
| **3. Hierarchical Retrieval** | `HierarchicalRetriever`: fact search (hybrid), BFS expansion, intent routing. Context assembly. Restore `ContextProvider` and Orchestrator tools. | 3-4 days | Full read path operational. Agent has memory again. |
| **4. Community Layer (Tier 3)** | `CommunityManager`: incremental assignment, summary generation, community search, weekly refresh schedule. | 2-3 days | High-level summaries for broad queries. |
| **5. Migration + Polish** | V2→V3 migration tool. Decay on facts. `memory_stats` adapted. End-to-end testing. | 2-3 days | Complete system, migration path available. |

**Phase 1 note:** Deploy the full schema from day one. Early phases only populate a subset of tables.

## Consequences

### Comparison with Memory v2

| Aspect | Memory v2 (ADR 008) | Memory v3 (this ADR) |
|--------|---------------------|---------------------|
| Storage model | Graph: `nodes` + `edges` + `entities` | Three-tier: `episodes` + `entities` + `facts` + `communities` |
| Fact representation | Text in `nodes.content` | Structured triples as edges (`subject_id`, `predicate`, `object_id`) |
| Node types | 4 (`episodic`, `semantic`, `procedural`, `opinion`) | 1 entity type (open taxonomy) + episodes in separate table |
| Write path | Full-episode consolidation via single LLM call | Atomic decomposition → parallel extraction → deterministic merge |
| Merge step | LLM-based conflict detection | Deterministic (cosine similarity + exact match) |
| FTS5 scope | All node content (episodes + facts) | Fact text only (compact, no episodic noise) |
| Abstraction layers | None (flat retrieval) | Communities: dynamic clusters with summaries |
| Temporal model | Bi-temporal on nodes (`event_time`, `valid_from/until`) | Dual-time on facts (`t_valid/invalid`, `t_created/expired`) + `invalidated_by` chain |
| Conflict detection | Hybrid search (fuzzy) | Structural (same entity pair + predicate) |
| Edge types | 5 (`temporal`, `causal`, `entity`, `derived_from`, `supersedes`) | Facts **are** the edges; `invalidated_by` replaces `supersedes` |
| Protocols | `ToolProvider` + `ContextProvider` + `SchedulerProvider` | Same |

### Benefits

- **Higher fact exhaustivity** — atomic decomposition catches facts that full-episode processing misses (+31% per ATOM benchmarks).
- **Structural conflict detection** — same entity pair with contradicting predicates is detected deterministically, not via fuzzy search.
- **Compact FTS5 index** — indexing only `fact_text` instead of all node content keeps the index relevant and fast at scale.
- **Constant-cost retrieval** — entity-centric model means retrieval cost scales with query complexity, not corpus size (NERD property).
- **Pre-computed community summaries** — broad queries ("tell me about X") are answered from Tier 3 without scanning individual facts.
- **Deterministic merge** — no LLM in the merge step means reproducible, fast, debuggable knowledge graph construction.
- **Richer provenance** — every fact has `source_episode_id` pointing to the raw episode and `invalidated_by` tracking supersession chains.

### Expected quantitative impact

| Metric | Memory v2 (baseline) | Memory v3 (expected) | Source |
|--------|---------------------|---------------------|--------|
| Retrieval accuracy (LongMemEval) | ~55% | ~71% | Zep benchmark |
| Response latency | baseline | **−90%** | Zep vs full-context |
| Context tokens per query | ~5000 | ~1600 | Zep avg context |
| Fact exhaustivity | baseline | **+31%** | ATOM decomposition |
| Construction stability | baseline | **+33%** | ATOM vs direct extraction |
| Scaling (100K+ episodes) | linear degradation | **constant cost** | NERD entity-centric |

### Risks and Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Atomic decomposition quality varies by model | Medium | Structured output (Pydantic) constrains format. Source episodes are immutable — re-run decomposition if quality is poor. Prompt engineering with few-shot examples. |
| Entity resolution false merges | Medium | Four-tier resolution strategy (§4.2): exact name → alias → high-confidence cosine (>0.85) → LLM disambiguation for ambiguous zone (0.70–0.85). Alias accumulation prevents repeat ambiguity. `invalidated_by` chains allow manual correction. Future `split_entity` tool for recourse. |
| LLM cost increase (more calls per episode) | Low | Atomic facts are short texts — cheap to process. Module 4 (merge) is free (no LLM). Net cost increase is ~2-3x per episode vs v2, still <$0.01/session with `gpt-4.1-mini`. |
| Migration re-processing time for large v2 databases | Low | Migration is a one-time batch job. Parallelizable across episodes. Alternative: clean start for small deployments. |
| Community summaries become stale | Low | Incremental updates on entity addition. Weekly full refresh. Staleness affects Tier 3 only — Tier 2 facts are always current. |
| Breaking change loses v2 knowledge | Medium | Migration tool re-derives all knowledge from episodes. V2 episodes (immutable audit trail) are fully preserved. Only LLM-derived semantic/procedural/opinion nodes are re-extracted. |

### Trade-offs

- **More LLM calls per write** — atomic decomposition adds 1 LLM call per episode (decomposition), plus N parallel calls for N atomic facts. Total cost per session is ~2-3x v2. Acceptable given the quality improvement and the use of cheap models.
- **Schema complexity** — more tables (7 vs 4), more indexes. Justified by the structural benefits (typed queries, compact FTS5, temporal chains).
- **No backward compatibility** — v2 data requires migration or clean start. Accepted trade-off — the schema changes are too fundamental for incremental migration.

## Alternatives Considered

**Incremental v2 enhancement (add `subject_id`/`object_id` to existing `edges`).** Rejected: the v2 `nodes` table conflates episodes and facts. Adding structured triples to edges while keeping facts as nodes creates a hybrid model with duplicate representations.

**Use Neo4j or another graph database.** Rejected: violates the embedded/local-first principle (ADR 002). SQLite with recursive CTEs handles the graph scale of a personal agent. The three-tier schema is a relational projection of the graph model that performs well up to millions of facts.

**Skip atomic decomposition, extract triples directly from episodes.** Rejected: ATOM benchmarks show +31% exhaustivity with atomic decomposition first. The intermediate step is essential for catching secondary facts in longer messages.

**Skip communities (Tier 3), keep only two tiers.** Considered: communities add implementation complexity and LLM cost for summary generation. Retained because community summaries solve a real problem — broad queries about a topic cluster are inefficient without pre-computed overviews. Implementation is deferred to Phase 4, and the system degrades gracefully without Tier 3.

**Use Graphiti directly as a library.** Rejected: Graphiti is tightly coupled to Neo4j. Adapting it to SQLite would require rewriting most of the storage layer. The architectural patterns (three-tier hierarchy, bi-temporal model, label propagation) are adopted; the implementation is native to the existing SQLite + sqlite-vec stack.

## Relation to Other ADRs

- **ADR 002 (Extensions)** — Memory v3 implements the same three protocols (`ToolProvider` + `ContextProvider` + `SchedulerProvider`). No changes to the extension contract.
- **ADR 003 (Agent-as-Extension)** — The write-path pipeline uses a private `Agent` instance via `ModelRouter`, same as v2.
- **ADR 004 (Event Bus)** — Subscribes to `user_message`, `agent_response`, `session.completed`. No changes to event topics.
- **ADR 008 (Memory v2)** — **Superseded.** This ADR replaces the flat-node model with a three-tier hierarchical graph where facts are structured edges.
- **ADR 009 (Timestamp format)** — Timestamp formatting for tool outputs is preserved. Facts expose `t_valid` and `t_created` through the same formatting utilities.

## References

- [ATOM: Adaptive and Optimized Temporal KG Construction](https://arxiv.org/html/2510.22590v2) — Atomic fact decomposition, parallel extraction, deterministic merge.
- [ATOM Review (TheMoonlight)](https://www.themoonlight.io/en/review/atom-adaptive-and-optimized-dynamic-temporal-knowledge-graph-construction-using-llms) — Benchmarks: +31% exhaustivity, +33% stability, −93% merge latency.
- [Graphiti: Temporal Knowledge Graphs for Agentic Applications](https://arxiv.org/pdf/2501.13956.pdf) — Three-tier hierarchy, bi-temporal model, community detection, Zep benchmarks.
- [Thinking Like a NERD: Entity-Centered Memory](https://d197for5662m48.cloudfront.net/documents/publicationstatus/308089/preprint_pdf/5523a14218223c82556c0021e16f834b.pdf) — Entity-centric scaling properties.
- [Hierarchical Memory Survey (arXiv 2602.05665)](https://arxiv.org/html/2602.05665v1) — Survey of SOTA memory architectures.
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — Vector search as SQLite extension.
- [Graphiti (GitHub)](https://github.com/getzep/graphiti) — Reference implementation for bi-temporal knowledge graphs.
- ADR 002: Nano-Kernel + Extensions
- ADR 003: Agent-as-Extension
- ADR 004: Event Bus in Core
- ADR 008: Memory v2 (superseded)
- ADR 009: Timestamp Output Format
