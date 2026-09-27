CREATE TABLE IF NOT EXISTS attention_index (
    attention_id TEXT PRIMARY KEY,
    policy_evaluation_id TEXT NOT NULL UNIQUE,
    owner_subject TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    FOREIGN KEY (policy_evaluation_id) REFERENCES policy_evaluations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_attention_index_owner
    ON attention_index(owner_subject, attention_id);
