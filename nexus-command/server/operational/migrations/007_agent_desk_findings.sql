-- Agent desk findings.
--
-- Until now a match produced exactly one finding, from the rule's own agent. That records what
-- the rule already said and nothing about the domains nobody could check. These columns let a
-- desk be recorded as deliberately silent, and force every contributing desk to name the
-- evidence behind its claim.

ALTER TABLE agent_findings
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'contributed',
  ADD COLUMN IF NOT EXISTS cited_evidence_ids uuid[] NOT NULL DEFAULT ARRAY[]::uuid[];

-- Findings written before desks cited evidence have an empty array. Attach the incident's
-- material evidence so a contributing row can satisfy the citation contract, then mark any
-- remaining empty contribution as abstained rather than inventing a citation.
UPDATE agent_findings f
   SET cited_evidence_ids = cited.ids
  FROM (
    SELECT ie.incident_id, array_agg(ie.evidence_id) AS ids
      FROM incident_evidence ie
     GROUP BY ie.incident_id
  ) cited
 WHERE f.incident_id = cited.incident_id
   AND cardinality(f.cited_evidence_ids) = 0
   AND f.agent_code <> 'nexus';

UPDATE agent_findings
   SET status = 'abstained'
 WHERE cardinality(cited_evidence_ids) = 0
   AND agent_code <> 'nexus'
   AND status = 'contributed';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_findings_status_check') THEN
    ALTER TABLE agent_findings
      ADD CONSTRAINT agent_findings_status_check CHECK (status IN ('contributed', 'abstained'));
  END IF;
END;
$$;

-- A contributing desk that cites nothing is an opinion, which is exactly what this system is not
-- allowed to produce. An abstaining desk cites nothing by definition.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'agent_findings_citation_check') THEN
    ALTER TABLE agent_findings
      ADD CONSTRAINT agent_findings_citation_check
      CHECK (status = 'abstained' OR agent_code = 'nexus' OR cardinality(cited_evidence_ids) > 0);
  END IF;
END;
$$;

-- One finding per desk per evidence snapshot. Detection re-runs every cycle and must be able to
-- upsert rather than accumulate duplicates.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_findings_desk_snapshot
  ON agent_findings(incident_id, agent_code, evidence_snapshot_hash);

CREATE INDEX IF NOT EXISTS idx_agent_findings_status
  ON agent_findings(incident_id, status);

COMMENT ON COLUMN agent_findings.status IS 'contributed = the desk had permitted evidence and spoke. abstained = the desk was staffed but had no feed or nothing relevant, which is itself reportable.';
COMMENT ON COLUMN agent_findings.cited_evidence_ids IS 'Evidence rows behind this finding. Required for any contributing domain desk.';
