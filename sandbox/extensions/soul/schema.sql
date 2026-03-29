PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS soul_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    state_json    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_type    TEXT NOT NULL,
    phase         TEXT NOT NULL,
    content       TEXT NOT NULL,
    payload_json  TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_traces_created_at
    ON traces(created_at);

CREATE INDEX IF NOT EXISTS idx_traces_phase_created_at
    ON traces(phase, created_at);

CREATE INDEX IF NOT EXISTS idx_traces_trace_type_created_at
    ON traces(trace_type, created_at);

CREATE TABLE IF NOT EXISTS interaction_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    direction         TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    channel_id        TEXT,
    hour              INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
    day_of_week       INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    outreach_result   TEXT CHECK (
        outreach_result IS NULL
        OR outreach_result IN ('response', 'ignored', 'timing_miss', 'rejected')
    ),
    message_length    INTEGER,
    openness_signal   REAL,
    response_delay_s  INTEGER,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interaction_log_created_at
    ON interaction_log(created_at);

CREATE INDEX IF NOT EXISTS idx_interaction_log_direction_created_at
    ON interaction_log(direction, created_at);

CREATE INDEX IF NOT EXISTS idx_interaction_log_hour_day
    ON interaction_log(hour, day_of_week);

CREATE INDEX IF NOT EXISTS idx_interaction_log_channel_created_at
    ON interaction_log(channel_id, created_at);

CREATE TABLE IF NOT EXISTS interaction_patterns (
    hour                      INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
    day_of_week               INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    interaction_count         INTEGER NOT NULL DEFAULT 0,
    inbound_count             INTEGER NOT NULL DEFAULT 0,
    outbound_count            INTEGER NOT NULL DEFAULT 0,
    response_count            INTEGER NOT NULL DEFAULT 0,
    ignored_count             INTEGER NOT NULL DEFAULT 0,
    timing_miss_count         INTEGER NOT NULL DEFAULT 0,
    rejected_count            INTEGER NOT NULL DEFAULT 0,
    avg_response_delay_s      REAL,
    response_delay_samples    INTEGER NOT NULL DEFAULT 0,
    updated_at                TEXT NOT NULL,
    PRIMARY KEY (hour, day_of_week)
);

CREATE TABLE IF NOT EXISTS channel_preferences (
    channel_id            TEXT PRIMARY KEY,
    interaction_count     INTEGER NOT NULL DEFAULT 0,
    inbound_count         INTEGER NOT NULL DEFAULT 0,
    outbound_count        INTEGER NOT NULL DEFAULT 0,
    last_interaction_at   TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationship_patterns (
    pattern_key            TEXT PRIMARY KEY,
    pattern_type           TEXT NOT NULL,
    content                TEXT NOT NULL,
    repetition_count       INTEGER NOT NULL DEFAULT 0,
    confidence             REAL NOT NULL DEFAULT 0.0,
    first_seen_at          TEXT NOT NULL,
    last_seen_at           TEXT NOT NULL,
    is_permanent           INTEGER NOT NULL DEFAULT 0,
    source_json            TEXT,
    updated_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_nodes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    topic         TEXT NOT NULL,
    content       TEXT NOT NULL,
    confidence    REAL NOT NULL DEFAULT 0.0,
    source_json   TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discovery_nodes_created_at
    ON discovery_nodes(created_at);

CREATE INDEX IF NOT EXISTS idx_discovery_nodes_topic_created_at
    ON discovery_nodes(topic, created_at);

CREATE TABLE IF NOT EXISTS soul_metrics (
    date                      TEXT PRIMARY KEY,
    outreach_attempts         INTEGER NOT NULL DEFAULT 0,
    outreach_responses        INTEGER NOT NULL DEFAULT 0,
    outreach_ignored          INTEGER NOT NULL DEFAULT 0,
    outreach_timing_miss      INTEGER NOT NULL DEFAULT 0,
    outreach_rejected         INTEGER NOT NULL DEFAULT 0,
    message_count             INTEGER NOT NULL DEFAULT 0,
    inference_count           INTEGER NOT NULL DEFAULT 0,
    reflection_count          INTEGER NOT NULL DEFAULT 0,
    perception_corrections    INTEGER NOT NULL DEFAULT 0,
    phase_distribution_json   TEXT,
    openness_avg              REAL,
    context_words_avg         REAL,
    context_words_samples     INTEGER NOT NULL DEFAULT 0,
    updated_at                TEXT NOT NULL
);
