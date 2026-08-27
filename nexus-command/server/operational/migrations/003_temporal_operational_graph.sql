BEGIN;

CREATE TABLE IF NOT EXISTS graph_ingestion_batches (
  graph_batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES operational_events(event_id) ON DELETE CASCADE,
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  source_id uuid NOT NULL REFERENCES sources(source_id),
  request_id text NOT NULL,
  schema_version text NOT NULL,
  payload_hash text NOT NULL,
  status text NOT NULL CHECK (status IN ('processing', 'succeeded', 'partial', 'failed')),
  node_count integer NOT NULL DEFAULT 0 CHECK (node_count >= 0),
  edge_count integer NOT NULL DEFAULT 0 CHECK (edge_count >= 0),
  unchanged_count integer NOT NULL DEFAULT 0 CHECK (unchanged_count >= 0),
  rejected_count integer NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
  error_detail text,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE (source_id, request_id)
);

CREATE TABLE IF NOT EXISTS graph_nodes (
  graph_node_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES operational_events(event_id) ON DELETE CASCADE,
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  node_type text NOT NULL CHECK (length(node_type) BETWEEN 2 AND 80),
  external_key text NOT NULL CHECK (length(external_key) BETWEEN 1 AND 300),
  label text NOT NULL CHECK (length(label) BETWEEN 1 AND 300),
  owner_agency_id uuid REFERENCES agencies(agency_id),
  source_id uuid REFERENCES sources(source_id),
  authority_uri text,
  data_classification text NOT NULL DEFAULT 'operational'
    CHECK (data_classification IN ('live', 'near_real_time', 'reference', 'operational', 'restricted')),
  geometry_geojson jsonb,
  current_state jsonb NOT NULL DEFAULT '{}'::jsonb,
  quality_flags text[] NOT NULL DEFAULT ARRAY[]::text[],
  state_hash text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_until timestamptz,
  active boolean NOT NULL DEFAULT true,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (valid_until IS NULL OR valid_until > valid_from),
  UNIQUE (event_id, mode, node_type, external_key)
);

CREATE TABLE IF NOT EXISTS graph_edges (
  graph_edge_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES operational_events(event_id) ON DELETE CASCADE,
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  edge_type text NOT NULL CHECK (length(edge_type) BETWEEN 2 AND 80),
  external_key text NOT NULL CHECK (length(external_key) BETWEEN 1 AND 300),
  from_node_id uuid NOT NULL REFERENCES graph_nodes(graph_node_id) ON DELETE CASCADE,
  to_node_id uuid NOT NULL REFERENCES graph_nodes(graph_node_id) ON DELETE CASCADE,
  directed boolean NOT NULL DEFAULT true,
  owner_agency_id uuid REFERENCES agencies(agency_id),
  source_id uuid REFERENCES sources(source_id),
  authority_uri text,
  data_classification text NOT NULL DEFAULT 'operational'
    CHECK (data_classification IN ('live', 'near_real_time', 'reference', 'operational', 'restricted')),
  geometry_geojson jsonb,
  current_state jsonb NOT NULL DEFAULT '{}'::jsonb,
  quality_flags text[] NOT NULL DEFAULT ARRAY[]::text[],
  state_hash text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_until timestamptz,
  active boolean NOT NULL DEFAULT true,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (from_node_id <> to_node_id),
  CHECK (valid_until IS NULL OR valid_until > valid_from),
  UNIQUE (event_id, mode, edge_type, external_key)
);

CREATE TABLE IF NOT EXISTS graph_node_evidence (
  graph_node_id uuid NOT NULL REFERENCES graph_nodes(graph_node_id) ON DELETE CASCADE,
  evidence_id uuid NOT NULL REFERENCES evidence_events(evidence_id) ON DELETE CASCADE,
  relationship text NOT NULL DEFAULT 'observed_by'
    CHECK (relationship IN ('observed_by', 'located_by', 'status_from', 'verified_by', 'contradicted_by')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (graph_node_id, evidence_id, relationship)
);

CREATE TABLE IF NOT EXISTS graph_edge_evidence (
  graph_edge_id uuid NOT NULL REFERENCES graph_edges(graph_edge_id) ON DELETE CASCADE,
  evidence_id uuid NOT NULL REFERENCES evidence_events(evidence_id) ON DELETE CASCADE,
  relationship text NOT NULL DEFAULT 'observed_by'
    CHECK (relationship IN ('observed_by', 'located_by', 'status_from', 'verified_by', 'contradicted_by')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (graph_edge_id, evidence_id, relationship)
);

CREATE TABLE IF NOT EXISTS graph_state_changes (
  graph_state_change_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES operational_events(event_id) ON DELETE CASCADE,
  mode text NOT NULL CHECK (mode IN ('live', 'training', 'replay')),
  entity_kind text NOT NULL CHECK (entity_kind IN ('node', 'edge')),
  entity_id uuid NOT NULL,
  entity_type text NOT NULL,
  change_type text NOT NULL CHECK (change_type IN ('created', 'state_updated', 'geometry_updated', 'deactivated', 'reactivated')),
  previous_version integer,
  new_version integer NOT NULL CHECK (new_version > 0),
  previous_state jsonb,
  new_state jsonb NOT NULL,
  previous_geometry_geojson jsonb,
  new_geometry_geojson jsonb,
  quality_flags text[] NOT NULL DEFAULT ARRAY[]::text[],
  source_id uuid REFERENCES sources(source_id),
  evidence_ids uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
  actor_principal_id uuid REFERENCES principals(principal_id),
  request_id text,
  occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS graph_nodes_event_type_active_idx
  ON graph_nodes(event_id, mode, node_type, active, valid_from DESC);
CREATE INDEX IF NOT EXISTS graph_nodes_owner_idx
  ON graph_nodes(event_id, owner_agency_id) WHERE owner_agency_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS graph_nodes_state_gin_idx ON graph_nodes USING gin(current_state);
CREATE INDEX IF NOT EXISTS graph_nodes_geometry_gin_idx ON graph_nodes USING gin(geometry_geojson);
CREATE INDEX IF NOT EXISTS graph_edges_from_idx
  ON graph_edges(event_id, mode, from_node_id, edge_type, active);
CREATE INDEX IF NOT EXISTS graph_edges_to_idx
  ON graph_edges(event_id, mode, to_node_id, edge_type, active);
CREATE INDEX IF NOT EXISTS graph_edges_state_gin_idx ON graph_edges USING gin(current_state);
CREATE INDEX IF NOT EXISTS graph_state_changes_entity_time_idx
  ON graph_state_changes(entity_kind, entity_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS graph_state_changes_event_time_idx
  ON graph_state_changes(event_id, mode, occurred_at DESC);

CREATE OR REPLACE FUNCTION enforce_graph_node_mode()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE event_mode text;
BEGIN
  SELECT mode INTO event_mode FROM operational_events WHERE event_id = NEW.event_id;
  IF event_mode IS NULL OR event_mode <> NEW.mode THEN
    RAISE EXCEPTION 'Graph node mode does not match operational event mode';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    NEW.version := OLD.version + 1;
    NEW.updated_at := now();
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION enforce_graph_edge_scope()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE event_mode text; from_event uuid; to_event uuid; from_mode text; to_mode text;
BEGIN
  SELECT mode INTO event_mode FROM operational_events WHERE event_id = NEW.event_id;
  SELECT event_id, mode INTO from_event, from_mode FROM graph_nodes WHERE graph_node_id = NEW.from_node_id;
  SELECT event_id, mode INTO to_event, to_mode FROM graph_nodes WHERE graph_node_id = NEW.to_node_id;
  IF event_mode IS NULL OR event_mode <> NEW.mode THEN
    RAISE EXCEPTION 'Graph edge mode does not match operational event mode';
  END IF;
  IF from_event <> NEW.event_id OR to_event <> NEW.event_id OR from_mode <> NEW.mode OR to_mode <> NEW.mode THEN
    RAISE EXCEPTION 'Graph edge endpoints must belong to the same event and mode';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    NEW.version := OLD.version + 1;
    NEW.updated_at := now();
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION capture_graph_node_change()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text;
BEGIN
  kind := CASE
    WHEN TG_OP = 'INSERT' THEN 'created'
    WHEN OLD.active AND NOT NEW.active THEN 'deactivated'
    WHEN NOT OLD.active AND NEW.active THEN 'reactivated'
    WHEN OLD.geometry_geojson IS DISTINCT FROM NEW.geometry_geojson THEN 'geometry_updated'
    ELSE 'state_updated'
  END;
  IF TG_OP = 'INSERT' OR OLD.state_hash IS DISTINCT FROM NEW.state_hash
     OR OLD.geometry_geojson IS DISTINCT FROM NEW.geometry_geojson OR OLD.active IS DISTINCT FROM NEW.active THEN
    INSERT INTO graph_state_changes (
      event_id, mode, entity_kind, entity_id, entity_type, change_type,
      previous_version, new_version, previous_state, new_state,
      previous_geometry_geojson, new_geometry_geojson, quality_flags, source_id, request_id
    ) VALUES (
      NEW.event_id, NEW.mode, 'node', NEW.graph_node_id, NEW.node_type, kind,
      CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.version END, NEW.version,
      CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.current_state END, NEW.current_state,
      CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.geometry_geojson END, NEW.geometry_geojson,
      NEW.quality_flags, NEW.source_id, nullif(current_setting('nexus.request_id', true), '')
    );
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION capture_graph_edge_change()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text;
BEGIN
  kind := CASE
    WHEN TG_OP = 'INSERT' THEN 'created'
    WHEN OLD.active AND NOT NEW.active THEN 'deactivated'
    WHEN NOT OLD.active AND NEW.active THEN 'reactivated'
    WHEN OLD.geometry_geojson IS DISTINCT FROM NEW.geometry_geojson THEN 'geometry_updated'
    ELSE 'state_updated'
  END;
  IF TG_OP = 'INSERT' OR OLD.state_hash IS DISTINCT FROM NEW.state_hash
     OR OLD.geometry_geojson IS DISTINCT FROM NEW.geometry_geojson OR OLD.active IS DISTINCT FROM NEW.active THEN
    INSERT INTO graph_state_changes (
      event_id, mode, entity_kind, entity_id, entity_type, change_type,
      previous_version, new_version, previous_state, new_state,
      previous_geometry_geojson, new_geometry_geojson, quality_flags, source_id, request_id
    ) VALUES (
      NEW.event_id, NEW.mode, 'edge', NEW.graph_edge_id, NEW.edge_type, kind,
      CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.version END, NEW.version,
      CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.current_state END, NEW.current_state,
      CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.geometry_geojson END, NEW.geometry_geojson,
      NEW.quality_flags, NEW.source_id, nullif(current_setting('nexus.request_id', true), '')
    );
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION reject_graph_history_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'graph_state_changes is append-only';
END;
$$;

DROP TRIGGER IF EXISTS graph_nodes_mode_guard ON graph_nodes;
CREATE TRIGGER graph_nodes_mode_guard BEFORE INSERT OR UPDATE ON graph_nodes
FOR EACH ROW EXECUTE FUNCTION enforce_graph_node_mode();
DROP TRIGGER IF EXISTS graph_edges_scope_guard ON graph_edges;
CREATE TRIGGER graph_edges_scope_guard BEFORE INSERT OR UPDATE ON graph_edges
FOR EACH ROW EXECUTE FUNCTION enforce_graph_edge_scope();
DROP TRIGGER IF EXISTS graph_nodes_history ON graph_nodes;
CREATE TRIGGER graph_nodes_history AFTER INSERT OR UPDATE ON graph_nodes
FOR EACH ROW EXECUTE FUNCTION capture_graph_node_change();
DROP TRIGGER IF EXISTS graph_edges_history ON graph_edges;
CREATE TRIGGER graph_edges_history AFTER INSERT OR UPDATE ON graph_edges
FOR EACH ROW EXECUTE FUNCTION capture_graph_edge_change();
DROP TRIGGER IF EXISTS graph_state_changes_append_only ON graph_state_changes;
CREATE TRIGGER graph_state_changes_append_only BEFORE UPDATE OR DELETE ON graph_state_changes
FOR EACH ROW EXECUTE FUNCTION reject_graph_history_mutation();

COMMIT;
