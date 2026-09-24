ALTER TABLE policy_evaluations
    ADD COLUMN ecosystem TEXT NOT NULL DEFAULT 'PyPI'
    CHECK (ecosystem IN ('PyPI', 'npm'));

CREATE INDEX IF NOT EXISTS ix_policy_evaluations_ecosystem_package
    ON policy_evaluations(ecosystem, package, version, evaluated_at_ms DESC);
