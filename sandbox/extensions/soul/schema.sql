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
    response_delay_s  INTEGER,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interaction_log_created_at
    ON interaction_log(created_at);

CREATE INDEX IF NOT EXISTS idx_interaction_log_hour_day
    ON interaction_log(hour, day_of_week);

CREATE INDEX IF NOT EXISTS idx_interaction_log_channel_created_at
    ON interaction_log(channel_id, created_at);

CREATE TABLE IF NOT EXISTS soul_metrics (
    date                      TEXT PRIMARY KEY,
    outreach_attempts         INTEGER NOT NULL DEFAULT 0,
    outreach_responses        INTEGER NOT NULL DEFAULT 0,
    outreach_ignored          INTEGER NOT NULL DEFAULT 0,
    outreach_timing_miss      INTEGER NOT NULL DEFAULT 0,
    outreach_rejected         INTEGER NOT NULL DEFAULT 0,
    message_count             INTEGER NOT NULL DEFAULT 0,
    inference_count           INTEGER NOT NULL DEFAULT 0,
    perception_corrections    INTEGER NOT NULL DEFAULT 0,
    phase_distribution_json   TEXT,
    openness_avg              REAL,
    updated_at                TEXT NOT NULL
);
