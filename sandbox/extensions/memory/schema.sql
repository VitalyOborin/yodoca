-- ==========================================================================
-- Memory v2: Graph-based cognitive memory (ADR 008)
-- ==========================================================================

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
-- Session consolidation tracking
-- ==========================================================================
CREATE TABLE IF NOT EXISTS sessions_consolidations (
    session_id       TEXT PRIMARY KEY,
    first_seen_at    INTEGER NOT NULL,
    consolidated_at  INTEGER
);

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
