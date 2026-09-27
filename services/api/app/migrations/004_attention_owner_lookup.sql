CREATE INDEX IF NOT EXISTS ix_policy_evaluations_owner_time
    ON policy_evaluations(owner_subject, evaluated_at_ms DESC, id DESC);
