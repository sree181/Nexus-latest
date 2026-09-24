PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS developer_sessions (
    id TEXT PRIMARY KEY,
    owner_subject TEXT NOT NULL,
    owner_name TEXT NOT NULL,
    adapter TEXT NOT NULL CHECK (adapter IN ('cursor', 'claude-code')),
    adapter_version TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    repository_name TEXT NOT NULL,
    repository_remote TEXT,
    repository_branch TEXT,
    repository_commit TEXT,
    task TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('starting', 'active', 'ending', 'completed', 'failed')),
    started_at_ms INTEGER NOT NULL,
    last_seen_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER,
    next_sequence INTEGER NOT NULL DEFAULT 2 CHECK (next_sequence >= 2),
    last_acked_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_acked_sequence >= 0),
    end_sequence INTEGER,
    run_id TEXT UNIQUE,
    device_id TEXT,
    verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
    failure_reason TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (owner_subject, adapter, repository_id, source_session_id)
);

CREATE INDEX IF NOT EXISTS ix_developer_sessions_owner_updated
    ON developer_sessions(owner_subject, updated_at_ms DESC);
CREATE INDEX IF NOT EXISTS ix_developer_sessions_repository
    ON developer_sessions(repository_id, updated_at_ms DESC);
CREATE INDEX IF NOT EXISTS ix_developer_sessions_status
    ON developer_sessions(status, updated_at_ms DESC);

CREATE TABLE IF NOT EXISTS activity_events (
    event_id TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES developer_sessions(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'session.started', 'prompt.submitted', 'tool.started', 'tool.completed',
        'tool.failed', 'file.changed', 'decision.recorded', 'package.requested',
        'package.installed', 'policy.evaluated', 'response.completed', 'session.ended'
    )),
    occurred_at_ms INTEGER NOT NULL,
    received_at_ms INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    projection_status TEXT NOT NULL DEFAULT 'pending' CHECK (projection_status IN (
        'pending', 'projecting', 'projected', 'refused', 'failed'
    )),
    projection_attempts INTEGER NOT NULL DEFAULT 0,
    projected_at_ms INTEGER,
    projection_error TEXT,
    run_id TEXT,
    created_at_ms INTEGER NOT NULL,
    UNIQUE (session_id, sequence),
    UNIQUE (session_id, source_event_id)
);

CREATE INDEX IF NOT EXISTS ix_activity_events_session_sequence
    ON activity_events(session_id, sequence);
CREATE INDEX IF NOT EXISTS ix_activity_events_projection
    ON activity_events(projection_status, received_at_ms);
CREATE INDEX IF NOT EXISTS ix_activity_events_type
    ON activity_events(event_type, received_at_ms DESC);

CREATE TABLE IF NOT EXISTS policy_evaluations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES developer_sessions(id) ON DELETE CASCADE,
    activity_event_id TEXT NOT NULL UNIQUE REFERENCES activity_events(event_id) ON DELETE CASCADE,
    package TEXT NOT NULL,
    version TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('allow', 'warn', 'block', 'unknown')),
    reasons_json TEXT NOT NULL,
    advisories_json TEXT NOT NULL,
    worst TEXT,
    unavailable TEXT,
    policy TEXT NOT NULL,
    evaluated_at_ms INTEGER NOT NULL,
    owner_subject TEXT NOT NULL,
    device_id TEXT,
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_policy_evaluations_session_time
    ON policy_evaluations(session_id, evaluated_at_ms DESC);
CREATE INDEX IF NOT EXISTS ix_policy_evaluations_verdict_time
    ON policy_evaluations(verdict, evaluated_at_ms DESC);
