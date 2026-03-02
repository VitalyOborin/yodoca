-- ==========================================================================
-- Memory v3: Hierarchical knowledge graph (ADR 016)
-- ==========================================================================

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
