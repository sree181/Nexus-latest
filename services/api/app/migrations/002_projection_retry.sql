ALTER TABLE developer_sessions
    ADD COLUMN projection_session_key TEXT;

UPDATE developer_sessions
    SET projection_session_key = CASE
        WHEN run_id IS NOT NULL OR EXISTS (
            SELECT 1 FROM activity_events
            WHERE activity_events.session_id = developer_sessions.id
              AND activity_events.projection_attempts > 0
        ) THEN source_session_id
        ELSE id
    END
    WHERE projection_session_key IS NULL;

CREATE INDEX IF NOT EXISTS ix_developer_sessions_projection_key
    ON developer_sessions(owner_subject, projection_session_key);

ALTER TABLE activity_events
    ADD COLUMN projection_last_attempt_at_ms INTEGER;

ALTER TABLE activity_events
    ADD COLUMN projection_next_attempt_at_ms INTEGER;

CREATE INDEX IF NOT EXISTS ix_activity_events_projection_retry
    ON activity_events(projection_status, projection_next_attempt_at_ms, received_at_ms);
