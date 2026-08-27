CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS agencies (
  agency_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE,
  name text NOT NULL,
  agency_type text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS principals (
  principal_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  external_subject text NOT NULL UNIQUE,
  display_name text NOT NULL,
  email text,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS principal_roles (
  principal_role_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  principal_id uuid NOT NULL REFERENCES principals(principal_id),
  agency_id uuid NOT NULL REFERENCES agencies(agency_id),
  role_code text NOT NULL,
  scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
  valid_from timestamptz NOT NULL DEFAULT now(),
  valid_until timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (principal_id, agency_id, role_code)
);

CREATE TABLE IF NOT EXISTS operational_events (
  event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  event_type text NOT NULL,
  name text NOT NULL,
  phase text NOT NULL CHECK (phase IN ('readiness', 'arrival', 'ingress', 'in_game', 'egress', 'after_action', 'closed')),
  status text NOT NULL CHECK (status IN ('planned', 'active', 'monitoring', 'closed', 'cancelled')),
  starts_at timestamptz NOT NULL,
  ends_at timestamptz,
  command_owner_principal_id uuid REFERENCES principals(principal_id),
  command_owner_agency_id uuid REFERENCES agencies(agency_id),
  location_name text NOT NULL,
  boundary_geojson jsonb,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS event_participants (
  event_participant_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES operational_events(event_id),
  agency_id uuid NOT NULL REFERENCES agencies(agency_id),
  operational_role text NOT NULL,
  primary_contact_principal_id uuid REFERENCES principals(principal_id),
  status text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'confirmed', 'active', 'released')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, agency_id, operational_role)
);

CREATE TABLE IF NOT EXISTS sources (
  source_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_agency_id uuid NOT NULL REFERENCES agencies(agency_id),
  code text NOT NULL UNIQUE,
  name text NOT NULL,
  source_type text NOT NULL CHECK (source_type IN ('api', 'webhook', 'poll', 'operator', 'file', 'derived')),
  schema_version text NOT NULL,
  expected_cadence_seconds integer CHECK (expected_cadence_seconds IS NULL OR expected_cadence_seconds > 0),
  stale_after_seconds integer NOT NULL CHECK (stale_after_seconds > 0),
  permitted_use text NOT NULL,
  health_status text NOT NULL DEFAULT 'unverified' CHECK (health_status IN ('healthy', 'delayed', 'unavailable', 'unverified', 'disabled')),
  last_success_at timestamptz,
  last_event_observed_at timestamptz,
  error_category text,
  config_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence_events (
  evidence_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES operational_events(event_id),
  source_id uuid NOT NULL REFERENCES sources(source_id),
  source_event_id text NOT NULL,
  schema_version text NOT NULL,
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  summary text NOT NULL,
  geometry_geojson jsonb,
  normalized_attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  quality_flags text[] NOT NULL DEFAULT ARRAY[]::text[],
  raw_object_key text,
  content_hash text NOT NULL,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, source_event_id)
);

CREATE TABLE IF NOT EXISTS incidents (
  incident_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES operational_events(event_id),
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  title text NOT NULL,
  what_changed text NOT NULL,
  why_it_matters text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'informational')),
  status text NOT NULL CHECK (status IN ('new', 'triaged', 'active', 'monitoring', 'resolved', 'closed')),
  command_owner_principal_id uuid REFERENCES principals(principal_id),
  command_owner_agency_id uuid REFERENCES agencies(agency_id),
  location_geojson jsonb,
  affected_services text[] NOT NULL DEFAULT ARRAY[]::text[],
  constraints text[] NOT NULL DEFAULT ARRAY[]::text[],
  detected_at timestamptz NOT NULL,
  resolved_at timestamptz,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS incident_evidence (
  incident_id uuid NOT NULL REFERENCES incidents(incident_id),
  evidence_id uuid NOT NULL REFERENCES evidence_events(evidence_id),
  material boolean NOT NULL DEFAULT true,
  attached_by_principal_id uuid REFERENCES principals(principal_id),
  attached_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (incident_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS agent_findings (
  finding_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id uuid NOT NULL REFERENCES incidents(incident_id),
  agent_code text NOT NULL,
  model_name text NOT NULL,
  model_version text NOT NULL,
  incident_snapshot_version integer NOT NULL CHECK (incident_snapshot_version > 0),
  evidence_snapshot_hash text NOT NULL,
  observation text NOT NULL,
  interpretation text NOT NULL,
  candidate_action text NOT NULL,
  confidence numeric(5,4) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  limitations text NOT NULL,
  conflicts jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recommendations (
  recommendation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id uuid NOT NULL REFERENCES incidents(incident_id),
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  recommendation_version integer NOT NULL CHECK (recommendation_version > 0),
  state text NOT NULL CHECK (state IN ('draft', 'awaiting_acknowledgement', 'awaiting_approval', 'approved', 'rejected', 'revision_requested', 'delegated', 'escalated', 'expired', 'superseded')),
  priority text NOT NULL CHECK (priority IN ('critical', 'high', 'medium', 'low', 'informational')),
  what_changed text NOT NULL,
  why_it_matters text NOT NULL,
  recommended_action text NOT NULL,
  expected_effect text NOT NULL,
  limitations text NOT NULL,
  constraints text[] NOT NULL DEFAULT ARRAY[]::text[],
  commitment_plan jsonb NOT NULL DEFAULT '[]'::jsonb,
  evidence_snapshot_hash text NOT NULL,
  generated_by_model text NOT NULL,
  generated_by_model_version text NOT NULL,
  expires_at timestamptz NOT NULL,
  superseded_by_recommendation_id uuid REFERENCES recommendations(recommendation_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (incident_id, recommendation_version)
);

CREATE TABLE IF NOT EXISTS recommendation_evidence (
  recommendation_id uuid NOT NULL REFERENCES recommendations(recommendation_id),
  evidence_id uuid NOT NULL REFERENCES evidence_events(evidence_id),
  role text NOT NULL CHECK (role IN ('material', 'supporting', 'conflicting')),
  PRIMARY KEY (recommendation_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS approval_requirements (
  approval_requirement_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recommendation_id uuid NOT NULL REFERENCES recommendations(recommendation_id),
  agency_id uuid NOT NULL REFERENCES agencies(agency_id),
  role_code text NOT NULL,
  sequence integer NOT NULL DEFAULT 1 CHECK (sequence > 0),
  quorum integer NOT NULL DEFAULT 1 CHECK (quorum > 0),
  delegation_allowed boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'satisfied', 'waived', 'expired')),
  satisfied_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (recommendation_id, agency_id, role_code)
);

CREATE TABLE IF NOT EXISTS decisions (
  decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recommendation_id uuid NOT NULL REFERENCES recommendations(recommendation_id),
  recommendation_version integer NOT NULL CHECK (recommendation_version > 0),
  evidence_snapshot_hash text NOT NULL,
  actor_principal_id uuid NOT NULL REFERENCES principals(principal_id),
  actor_agency_id uuid NOT NULL REFERENCES agencies(agency_id),
  actor_role_code text NOT NULL,
  action text NOT NULL CHECK (action IN ('approve', 'reject', 'request_revision', 'delegate', 'escalate', 'acknowledge', 'withdraw')),
  reason_code text NOT NULL,
  comment text,
  confirmation_text_hash text,
  delegated_to_principal_id uuid REFERENCES principals(principal_id),
  escalated_to_role_code text,
  decided_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commitments (
  commitment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  incident_id uuid NOT NULL REFERENCES incidents(incident_id),
  recommendation_id uuid NOT NULL REFERENCES recommendations(recommendation_id),
  decision_id uuid NOT NULL REFERENCES decisions(decision_id),
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  owner_agency_id uuid NOT NULL REFERENCES agencies(agency_id),
  assignee_principal_id uuid REFERENCES principals(principal_id),
  requested_outcome text NOT NULL,
  state text NOT NULL CHECK (state IN ('requested', 'acknowledged', 'approved', 'executing', 'blocked', 'verified', 'failed', 'expired', 'cancelled')),
  due_at timestamptz,
  blocker text,
  verification_rule text NOT NULL,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commitment_transitions (
  transition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  commitment_id uuid NOT NULL REFERENCES commitments(commitment_id),
  from_state text NOT NULL,
  to_state text NOT NULL,
  actor_principal_id uuid NOT NULL REFERENCES principals(principal_id),
  reason_code text NOT NULL,
  comment text,
  evidence_ids uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS action_templates (
  action_template_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE,
  name text NOT NULL,
  owner_agency_id uuid NOT NULL REFERENCES agencies(agency_id),
  incident_types text[] NOT NULL DEFAULT ARRAY[]::text[],
  parameter_schema jsonb NOT NULL,
  required_scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
  execution_mode text NOT NULL CHECK (execution_mode IN ('manual', 'deep_link', 'connector')),
  connector_code text,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution_requests (
  execution_request_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  commitment_id uuid NOT NULL REFERENCES commitments(commitment_id),
  action_template_id uuid NOT NULL REFERENCES action_templates(action_template_id),
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  requested_by_principal_id uuid NOT NULL REFERENCES principals(principal_id),
  parameters jsonb NOT NULL,
  validation_hash text NOT NULL,
  status text NOT NULL CHECK (status IN ('validation_required', 'validated', 'manual_handoff', 'requested', 'accepted', 'executing', 'confirmed', 'failed', 'cancelled', 'timed_out')),
  external_receipt_id text,
  handoff_url text,
  error_summary text,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution_confirmations (
  confirmation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_request_id uuid NOT NULL REFERENCES execution_requests(execution_request_id),
  confirmation_type text NOT NULL CHECK (confirmation_type IN ('authoritative_system', 'accountable_operator')),
  confirmed_by_principal_id uuid REFERENCES principals(principal_id),
  confirmed_at timestamptz NOT NULL,
  outcome text NOT NULL,
  external_receipt_id text,
  evidence_ids uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_records (
  idempotency_key text PRIMARY KEY,
  principal_id uuid NOT NULL REFERENCES principals(principal_id),
  route_key text NOT NULL,
  request_hash text NOT NULL,
  response_status integer,
  response_body jsonb,
  resource_type text,
  resource_id uuid,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
  audit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  event_id uuid REFERENCES operational_events(event_id),
  incident_id uuid REFERENCES incidents(incident_id),
  actor_type text NOT NULL CHECK (actor_type IN ('human', 'service', 'connector', 'system')),
  actor_id text NOT NULL,
  actor_agency_id uuid REFERENCES agencies(agency_id),
  action text NOT NULL,
  object_type text NOT NULL,
  object_id text NOT NULL,
  object_version integer,
  request_id text NOT NULL,
  before_hash text,
  after_hash text,
  outcome text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outbox_events (
  outbox_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type text NOT NULL,
  aggregate_type text NOT NULL,
  aggregate_id uuid NOT NULL,
  aggregate_version integer,
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  payload jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  last_error text
);

CREATE OR REPLACE FUNCTION nexus_enforce_incident_mode()
RETURNS trigger AS $$
DECLARE parent_mode text;
BEGIN
  SELECT mode INTO parent_mode FROM operational_events WHERE event_id = NEW.event_id;
  IF parent_mode IS NULL OR parent_mode <> NEW.mode THEN
    RAISE EXCEPTION 'incident mode does not match operational event mode';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION nexus_enforce_recommendation_mode()
RETURNS trigger AS $$
DECLARE parent_mode text;
BEGIN
  SELECT mode INTO parent_mode FROM incidents WHERE incident_id = NEW.incident_id;
  IF parent_mode IS NULL OR parent_mode <> NEW.mode THEN
    RAISE EXCEPTION 'recommendation mode does not match incident mode';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION nexus_enforce_commitment_mode()
RETURNS trigger AS $$
DECLARE incident_mode text;
DECLARE recommendation_mode text;
BEGIN
  SELECT mode INTO incident_mode FROM incidents WHERE incident_id = NEW.incident_id;
  SELECT mode INTO recommendation_mode FROM recommendations WHERE recommendation_id = NEW.recommendation_id;
  IF incident_mode IS NULL OR recommendation_mode IS NULL OR incident_mode <> NEW.mode OR recommendation_mode <> NEW.mode THEN
    RAISE EXCEPTION 'commitment mode does not match incident and recommendation mode';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS enforce_incident_mode ON incidents;
CREATE TRIGGER enforce_incident_mode
BEFORE INSERT OR UPDATE OF event_id, mode ON incidents
FOR EACH ROW EXECUTE FUNCTION nexus_enforce_incident_mode();

DROP TRIGGER IF EXISTS enforce_recommendation_mode ON recommendations;
CREATE TRIGGER enforce_recommendation_mode
BEFORE INSERT OR UPDATE OF incident_id, mode ON recommendations
FOR EACH ROW EXECUTE FUNCTION nexus_enforce_recommendation_mode();

DROP TRIGGER IF EXISTS enforce_commitment_mode ON commitments;
CREATE TRIGGER enforce_commitment_mode
BEFORE INSERT OR UPDATE OF incident_id, recommendation_id, mode ON commitments
FOR EACH ROW EXECUTE FUNCTION nexus_enforce_commitment_mode();

CREATE OR REPLACE FUNCTION nexus_set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'agencies', 'principals', 'operational_events', 'event_participants',
    'sources', 'incidents', 'recommendations', 'commitments',
    'action_templates', 'execution_requests'
  ]
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS set_updated_at ON %I', table_name);
    EXECUTE format(
      'CREATE TRIGGER set_updated_at BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION nexus_set_updated_at()',
      table_name
    );
  END LOOP;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_operational_events_mode_status_starts ON operational_events(mode, status, starts_at);
CREATE INDEX IF NOT EXISTS idx_sources_health ON sources(health_status, active);
CREATE INDEX IF NOT EXISTS idx_evidence_event_time ON evidence_events(event_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_source_time ON evidence_events(source_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_attributes_gin ON evidence_events USING gin(normalized_attributes);
CREATE INDEX IF NOT EXISTS idx_incidents_event_status_severity ON incidents(event_id, status, severity, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_incident_evidence_evidence ON incident_evidence(evidence_id);
CREATE INDEX IF NOT EXISTS idx_findings_incident_created ON agent_findings(incident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendations_incident_state ON recommendations(incident_id, state, recommendation_version DESC);
CREATE INDEX IF NOT EXISTS idx_approval_requirements_queue ON approval_requirements(status, agency_id, role_code);
CREATE INDEX IF NOT EXISTS idx_decisions_recommendation_time ON decisions(recommendation_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_commitments_incident_state ON commitments(incident_id, state, due_at);
CREATE INDEX IF NOT EXISTS idx_execution_commitment_status ON execution_requests(commitment_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_idempotency_expiry ON idempotency_records(expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_incident_sequence ON audit_events(incident_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_audit_event_sequence ON audit_events(event_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_outbox_unpublished ON outbox_events(occurred_at) WHERE published_at IS NULL;

COMMENT ON TABLE recommendations IS 'Evidence-bound advisory proposals. Approval does not execute an agency action.';
COMMENT ON TABLE decisions IS 'Human or authorized service decisions bound to an exact recommendation and evidence snapshot.';
COMMENT ON TABLE execution_requests IS 'Allowlisted execution handoffs. Stage 1 uses manual or deep-link modes only.';
COMMENT ON TABLE audit_events IS 'Append-only accountable operational history; application roles must not have UPDATE or DELETE permission.';

/* Legacy patch artifact below is intentionally ignored by PostgreSQL.
*** Add File: /home/ubuntu/nexus-command/server/operational/database.ts
import pg from 'pg';

const { Pool } = pg;

export type DatabasePool = pg.Pool;
export type DatabaseClient = pg.PoolClient;

let pool: pg.Pool | null = null;

export function hasDatabaseConfiguration(): boolean {
  return Boolean(process.env.DATABASE_URL);
}

export function getDatabasePool(): pg.Pool {
  if (!process.env.DATABASE_URL) {
    throw new Error('DATABASE_URL is required for persistent Nexus operational workflows');
  }

  if (!pool) {
    const sslEnabled = process.env.PGSSL === 'true';
    const rejectUnauthorized = process.env.PGSSL_REJECT_UNAUTHORIZED !== 'false';

    pool = new Pool({
      connectionString: process.env.DATABASE_URL,
      max: Number(process.env.DB_POOL_MAX || 10),
      idleTimeoutMillis: Number(process.env.DB_IDLE_TIMEOUT_MS || 30_000),
      connectionTimeoutMillis: Number(process.env.DB_CONNECT_TIMEOUT_MS || 5_000),
      ssl: sslEnabled ? { rejectUnauthorized } : undefined,
      application_name: 'nexus-coordinate',
    });

    pool.on('error', error => {
      console.error('[database] Unexpected idle client error', error);
    });
  }

  return pool;
}

export async function withTransaction<T>(operation: (client: pg.PoolClient) => Promise<T>): Promise<T> {
  const client = await getDatabasePool().connect();
  try {
    await client.query('BEGIN');
    const result = await operation(client);
    await client.query('COMMIT');
    return result;
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}

export async function closeDatabasePool(): Promise<void> {
  if (pool) {
    await pool.end();
    pool = null;
  }
}

*** Add File: /home/ubuntu/nexus-command/server/operational/migrate.ts
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { closeDatabasePool, getDatabasePool } from './database.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const migrationsDirectory = path.join(__dirname, 'migrations');

async function migrate(): Promise<void> {
  const pool = getDatabasePool();
  const client = await pool.connect();

  try {
    await client.query(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
        migration_id text PRIMARY KEY,
        applied_at timestamptz NOT NULL DEFAULT now()
      )
    `);

    const migrations = (await readdir(migrationsDirectory))
      .filter(file => file.endsWith('.sql'))
      .sort();

    for (const migration of migrations) {
      const existing = await client.query(
        'SELECT migration_id FROM schema_migrations WHERE migration_id = $1',
        [migration],
      );

      if (existing.rowCount) {
        console.log(`[migrate] ${migration} already applied`);
        continue;
      }

      const sql = await readFile(path.join(migrationsDirectory, migration), 'utf8');
      await client.query('BEGIN');
      try {
        await client.query(sql);
        await client.query('INSERT INTO schema_migrations (migration_id) VALUES ($1)', [migration]);
        await client.query('COMMIT');
        console.log(`[migrate] applied ${migration}`);
      } catch (error) {
        await client.query('ROLLBACK');
        throw error;
      }
    }
  } finally {
    client.release();
    await closeDatabasePool();
  }
}

migrate().catch(error => {
  console.error('[migrate] failed', error);
  process.exitCode = 1;
});
*/
