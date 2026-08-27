BEGIN;

ALTER TABLE sources
  ADD COLUMN IF NOT EXISTS authority_uri text,
  ADD COLUMN IF NOT EXISTS connector_code text,
  ADD COLUMN IF NOT EXISTS data_classification text NOT NULL DEFAULT 'operational'
    CHECK (data_classification IN ('live', 'near_real_time', 'reference', 'operational', 'restricted')),
  ADD COLUMN IF NOT EXISTS partner_approval_required boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS connection_status text NOT NULL DEFAULT 'not_connected'
    CHECK (connection_status IN ('connected', 'not_connected', 'configuration_required', 'permission_required', 'disabled')),
  ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz,
  ADD COLUMN IF NOT EXISTS consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0);

CREATE UNIQUE INDEX IF NOT EXISTS sources_connector_code_unique
  ON sources(connector_code)
  WHERE connector_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS connector_checkpoints (
  source_id uuid PRIMARY KEY REFERENCES sources(source_id) ON DELETE CASCADE,
  cursor_value text,
  etag text,
  last_modified text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS connector_runs (
  connector_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id uuid NOT NULL REFERENCES sources(source_id),
  event_id uuid REFERENCES operational_events(event_id),
  request_id text NOT NULL,
  trigger_type text NOT NULL CHECK (trigger_type IN ('scheduled', 'manual', 'webhook', 'startup', 'retry')),
  status text NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'skipped', 'disabled')),
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  upstream_observed_at timestamptz,
  fetched_count integer NOT NULL DEFAULT 0 CHECK (fetched_count >= 0),
  accepted_count integer NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
  duplicate_count integer NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
  rejected_count integer NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
  http_status integer,
  duration_ms integer CHECK (duration_ms IS NULL OR duration_ms >= 0),
  error_category text,
  error_detail text,
  checkpoint_before jsonb NOT NULL DEFAULT '{}'::jsonb,
  checkpoint_after jsonb NOT NULL DEFAULT '{}'::jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, request_id)
);

CREATE INDEX IF NOT EXISTS connector_runs_source_started_idx
  ON connector_runs(source_id, started_at DESC);

ALTER TABLE evidence_events
  ADD COLUMN IF NOT EXISTS connector_run_id uuid REFERENCES connector_runs(connector_run_id),
  ADD COLUMN IF NOT EXISTS authority_uri text,
  ADD COLUMN IF NOT EXISTS provenance jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS evidence_events_event_observed_idx
  ON evidence_events(event_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS evidence_events_source_observed_idx
  ON evidence_events(source_id, observed_at DESC);

COMMIT;
