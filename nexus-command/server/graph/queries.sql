-- 1. Current mobility subgraph for a live event.
SELECT jsonb_build_object(
  'nodes', COALESCE(jsonb_agg(DISTINCT jsonb_build_object(
    'id', n.graph_node_id, 'type', n.node_type, 'externalKey', n.external_key,
    'label', n.label, 'ownerAgencyId', n.owner_agency_id, 'geometry', n.geometry_geojson,
    'state', n.current_state, 'qualityFlags', n.quality_flags, 'version', n.version,
    'validFrom', n.valid_from, 'validUntil', n.valid_until
  )) FILTER (WHERE n.graph_node_id IS NOT NULL), '[]'::jsonb),
  'edges', COALESCE(jsonb_agg(DISTINCT jsonb_build_object(
    'id', e.graph_edge_id, 'type', e.edge_type, 'externalKey', e.external_key,
    'from', e.from_node_id, 'to', e.to_node_id, 'directed', e.directed,
    'geometry', e.geometry_geojson, 'state', e.current_state,
    'qualityFlags', e.quality_flags, 'version', e.version
  )) FILTER (WHERE e.graph_edge_id IS NOT NULL), '[]'::jsonb)
) AS graph
FROM graph_nodes n
LEFT JOIN graph_edges e ON e.event_id=n.event_id AND e.mode=n.mode AND e.active=true
WHERE n.event_id=$1 AND n.mode=$2 AND n.active=true
  AND n.node_type = ANY($3::text[])
  AND n.valid_from <= $4::timestamptz
  AND (n.valid_until IS NULL OR n.valid_until > $4::timestamptz);

-- 2. Bounded downstream impact paths from one node.
WITH RECURSIVE impact AS (
  SELECT e.graph_edge_id, e.from_node_id, e.to_node_id, e.edge_type,
         ARRAY[e.from_node_id, e.to_node_id]::uuid[] AS path, 1 AS depth
  FROM graph_edges e
  WHERE e.event_id=$1 AND e.mode=$2 AND e.active=true AND e.from_node_id=$3
  UNION ALL
  SELECT e.graph_edge_id, e.from_node_id, e.to_node_id, e.edge_type,
         i.path || e.to_node_id, i.depth+1
  FROM impact i
  JOIN graph_edges e ON e.from_node_id=i.to_node_id AND e.event_id=$1 AND e.mode=$2 AND e.active=true
  WHERE i.depth < LEAST($4::integer, 6) AND NOT e.to_node_id = ANY(i.path)
)
SELECT i.*, n.label AS affected_label, n.node_type AS affected_type, n.current_state
FROM impact i JOIN graph_nodes n ON n.graph_node_id=i.to_node_id
ORDER BY i.depth, n.label;

-- 3. Evidence provenance for a node, including connector authority and run.
SELECT n.graph_node_id, n.label, s.name AS source_name, s.authority_uri,
       ee.evidence_id, ee.source_event_id, ee.observed_at, ee.received_at,
       ee.summary, ee.quality_flags, ee.provenance, cr.connector_run_id, cr.status AS run_status
FROM graph_nodes n
JOIN graph_node_evidence gne ON gne.graph_node_id=n.graph_node_id
JOIN evidence_events ee ON ee.evidence_id=gne.evidence_id
JOIN sources s ON s.source_id=ee.source_id
LEFT JOIN connector_runs cr ON cr.connector_run_id=ee.connector_run_id
WHERE n.graph_node_id=$1
ORDER BY ee.observed_at DESC;

-- 4. State-change history for a node or edge.
SELECT graph_state_change_id, entity_kind, entity_id, entity_type, change_type,
       previous_version, new_version, previous_state, new_state,
       previous_geometry_geojson, new_geometry_geojson, quality_flags,
       source_id, evidence_ids, actor_principal_id, request_id, occurred_at
FROM graph_state_changes
WHERE entity_kind=$1 AND entity_id=$2
ORDER BY occurred_at DESC
LIMIT LEAST($3::integer, 500);

-- 5. Decision lineage: evidence -> incident -> recommendation -> decision -> commitment -> verification.
SELECT jsonb_build_object(
  'recommendation', to_jsonb(r),
  'incident', to_jsonb(i),
  'evidence', COALESCE((SELECT jsonb_agg(to_jsonb(ee)) FROM recommendation_evidence re JOIN evidence_events ee USING(evidence_id) WHERE re.recommendation_id=r.recommendation_id),'[]'::jsonb),
  'decisions', COALESCE((SELECT jsonb_agg(to_jsonb(d) ORDER BY d.decided_at) FROM decisions d WHERE d.recommendation_id=r.recommendation_id),'[]'::jsonb),
  'commitments', COALESCE((SELECT jsonb_agg(to_jsonb(c)) FROM commitments c WHERE c.recommendation_id=r.recommendation_id),'[]'::jsonb),
  'verifications', COALESCE((SELECT jsonb_agg(to_jsonb(ec)) FROM execution_confirmations ec JOIN commitments c USING(commitment_id) WHERE c.recommendation_id=r.recommendation_id),'[]'::jsonb)
) AS lineage
FROM recommendations r JOIN incidents i USING(incident_id)
WHERE r.recommendation_id=$1;

-- 6. Agency coordination graph for unresolved commitments.
SELECT c.commitment_id, c.state, c.requested_outcome, c.due_at, c.blocker,
       owner.agency_id AS owner_agency_id, owner.name AS owner_agency_name,
       requester.agency_id AS requester_agency_id, requester.name AS requester_agency_name,
       r.recommendation_id, r.priority, i.incident_id, i.title AS incident_title
FROM commitments c
JOIN agencies owner ON owner.agency_id=c.owner_agency_id
JOIN decisions d ON d.decision_id=c.decision_id
LEFT JOIN agencies requester ON requester.agency_id=d.actor_agency_id
JOIN recommendations r ON r.recommendation_id=c.recommendation_id
JOIN incidents i ON i.incident_id=c.incident_id
WHERE i.event_id=$1 AND c.mode=$2
  AND c.state NOT IN ('verified','failed','expired','cancelled')
ORDER BY c.due_at NULLS LAST, r.priority, owner.name;

-- 7. Stale graph state requiring source or agency attention.
SELECT n.graph_node_id AS entity_id, 'node' AS entity_kind, n.node_type AS entity_type,
       n.label, n.valid_from, n.valid_until, s.name AS source_name, s.health_status,
       s.connection_status, n.quality_flags
FROM graph_nodes n LEFT JOIN sources s ON s.source_id=n.source_id
WHERE n.event_id=$1 AND n.mode=$2 AND n.active=true
  AND (n.valid_until <= now() OR 'stale'=ANY(n.quality_flags) OR s.health_status IN ('delayed','unavailable'))
UNION ALL
SELECT e.graph_edge_id, 'edge', e.edge_type, e.external_key, e.valid_from, e.valid_until,
       s.name, s.health_status, s.connection_status, e.quality_flags
FROM graph_edges e LEFT JOIN sources s ON s.source_id=e.source_id
WHERE e.event_id=$1 AND e.mode=$2 AND e.active=true
  AND (e.valid_until <= now() OR 'stale'=ANY(e.quality_flags) OR s.health_status IN ('delayed','unavailable'));
