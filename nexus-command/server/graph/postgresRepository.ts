import type { PoolClient } from 'pg';
import { canonicalHash } from '../connectors/normalization.js';
import type { OperationalMode } from '../operational/domain.js';
import { getDatabasePool, withTransaction } from '../operational/database.js';
import { OperationalError } from '../operational/errors.js';
import type {
  GraphEdge,
  GraphEntityKind,
  GraphIngestionBatch,
  GraphIngestionResult,
  GraphNode,
  GraphQueryContext,
  GraphSnapshot,
  GraphStateChange,
  GraphView,
} from './domain.js';
import type { GraphRepository } from './repository.js';

function iso(value: unknown): string {
  return value instanceof Date ? value.toISOString() : new Date(String(value)).toISOString();
}

function mapNode(row: Record<string, any>): GraphNode {
  return {
    nodeId: row.graph_node_id, eventId: row.event_id, mode: row.mode, nodeType: row.node_type,
    externalKey: row.external_key, label: row.label, ownerAgencyId: row.owner_agency_id ?? null,
    sourceId: row.source_id ?? null, authorityUri: row.authority_uri ?? null,
    dataClassification: row.data_classification, geometryGeojson: row.geometry_geojson ?? null,
    state: row.current_state ?? {}, qualityFlags: row.quality_flags ?? [], validFrom: iso(row.valid_from),
    validUntil: row.valid_until ? iso(row.valid_until) : null, active: row.active, version: row.version,
    updatedAt: iso(row.updated_at),
  };
}

function mapEdge(row: Record<string, any>): GraphEdge {
  return {
    edgeId: row.graph_edge_id, eventId: row.event_id, mode: row.mode, edgeType: row.edge_type,
    externalKey: row.external_key, fromNodeId: row.from_node_id, toNodeId: row.to_node_id,
    directed: row.directed, ownerAgencyId: row.owner_agency_id ?? null, sourceId: row.source_id ?? null,
    authorityUri: row.authority_uri ?? null, dataClassification: row.data_classification,
    geometryGeojson: row.geometry_geojson ?? null, state: row.current_state ?? {},
    qualityFlags: row.quality_flags ?? [], validFrom: iso(row.valid_from),
    validUntil: row.valid_until ? iso(row.valid_until) : null, active: row.active, version: row.version,
    updatedAt: iso(row.updated_at),
  };
}

function viewTypes(view: GraphView): string[] {
  if (view === 'mobility') return ['intersection','road_segment','parking_lot','transit_stop','transit_vehicle','transit_route','staging_area','emergency_gate','emergency_corridor','closure','detour'];
  if (view === 'decision_lineage') return ['evidence','finding','incident','recommendation','decision','commitment','verification'];
  return ['agency','operational_team','incident','recommendation','decision','commitment','verification'];
}

async function eventMode(client: PoolClient, eventId: string): Promise<OperationalMode> {
  const result = await client.query('SELECT mode FROM operational_events WHERE event_id=$1', [eventId]);
  if (!result.rowCount) throw new OperationalError(404, 'EVENT_NOT_FOUND', 'Operational event not found');
  return result.rows[0].mode;
}

export class PostgresGraphRepository implements GraphRepository {
  async ingestBatch(
    eventId: string,
    sourceId: string,
    batch: GraphIngestionBatch,
    context: GraphQueryContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<GraphIngestionResult> {
    return withTransaction(async client => {
      await client.query("SELECT set_config('nexus.request_id',$1,true)", [requestId]);
      const mode = await eventMode(client, eventId);
      if (mode !== batch.mode || mode !== context.mode || !context.principal.modes.includes(mode)) {
        throw new OperationalError(409, 'GRAPH_MODE_MISMATCH', 'Graph batch, event, and operator modes must match');
      }
      const source = await client.query(
        'SELECT source_id, owner_agency_id, active FROM sources WHERE source_id=$1 FOR UPDATE',
        [sourceId],
      );
      if (!source.rowCount || !source.rows[0].active) throw new OperationalError(404, 'SOURCE_NOT_FOUND', 'Active authoritative source not found');
      const canIngestAnySource = context.principal.roles.includes('event_mobility_lead') || context.principal.scopes.includes('graph:ingest:any_source');
      if (!canIngestAnySource && source.rows[0].owner_agency_id !== context.principal.agencyId) {
        throw new OperationalError(403, 'SOURCE_AUTHORITY_REQUIRED', 'Operator agency does not own this authoritative source');
      }

      const payloadHash = canonicalHash(batch);
      const existing = await client.query(
        `SELECT graph_batch_id, payload_hash, status, node_count, edge_count, unchanged_count, rejected_count
           FROM graph_ingestion_batches WHERE source_id=$1 AND request_id=$2 FOR UPDATE`,
        [sourceId, idempotencyKey],
      );
      if (existing.rowCount) {
        const row = existing.rows[0];
        if (row.payload_hash !== payloadHash) throw new OperationalError(409, 'IDEMPOTENCY_KEY_REUSED', 'Idempotency key was already used with a different graph payload');
        return { batchId: row.graph_batch_id, status: row.status, nodeCount: row.node_count, edgeCount: row.edge_count, unchangedCount: row.unchanged_count, rejectedCount: row.rejected_count, requestId };
      }

      const batchRow = await client.query(
        `INSERT INTO graph_ingestion_batches (event_id, mode, source_id, request_id, schema_version, payload_hash, status)
         VALUES ($1,$2,$3,$4,$5,$6,'processing') RETURNING graph_batch_id`,
        [eventId, mode, sourceId, idempotencyKey, batch.schemaVersion, payloadHash],
      );
      const batchId = batchRow.rows[0].graph_batch_id;
      let nodeCount = 0;
      let edgeCount = 0;
      let unchangedCount = 0;
      let rejectedCount = 0;

      for (const node of batch.nodes) {
        const hash = canonicalHash({ state: node.state, geometry: node.geometryGeojson ?? null, flags: node.qualityFlags ?? [], active: node.active ?? true, validUntil: node.validUntil ?? null });
        const result = await client.query(
          `INSERT INTO graph_nodes (
             event_id, mode, node_type, external_key, label, owner_agency_id, source_id, authority_uri,
             data_classification, geometry_geojson, current_state, quality_flags, state_hash,
             valid_from, valid_until, active
           ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12,$13,$14,$15,$16)
           ON CONFLICT (event_id, mode, node_type, external_key) DO UPDATE SET
             label=EXCLUDED.label, owner_agency_id=EXCLUDED.owner_agency_id, source_id=EXCLUDED.source_id,
             authority_uri=EXCLUDED.authority_uri, data_classification=EXCLUDED.data_classification,
             geometry_geojson=EXCLUDED.geometry_geojson, current_state=EXCLUDED.current_state,
             quality_flags=EXCLUDED.quality_flags, state_hash=EXCLUDED.state_hash,
             valid_from=EXCLUDED.valid_from, valid_until=EXCLUDED.valid_until, active=EXCLUDED.active
           WHERE graph_nodes.state_hash IS DISTINCT FROM EXCLUDED.state_hash
           RETURNING graph_node_id`,
          [eventId, mode, node.nodeType, node.externalKey, node.label, node.ownerAgencyId ?? null, sourceId,
           node.authorityUri ?? null, node.dataClassification,
           node.geometryGeojson ? JSON.stringify(node.geometryGeojson) : null, JSON.stringify(node.state),
           node.qualityFlags ?? [], hash, node.validFrom, node.validUntil ?? null, node.active ?? true],
        );
        const nodeId = result.rowCount ? result.rows[0].graph_node_id : (await client.query(
          'SELECT graph_node_id FROM graph_nodes WHERE event_id=$1 AND mode=$2 AND node_type=$3 AND external_key=$4',
          [eventId, mode, node.nodeType, node.externalKey],
        )).rows[0].graph_node_id;
        if (result.rowCount) nodeCount += 1; else unchangedCount += 1;
        for (const evidenceId of node.evidenceIds ?? []) {
          await client.query(
            `INSERT INTO graph_node_evidence (graph_node_id, evidence_id, relationship)
             SELECT $1, evidence_id, 'observed_by' FROM evidence_events WHERE evidence_id=$2 AND event_id=$3
             ON CONFLICT DO NOTHING`,
            [nodeId, evidenceId, eventId],
          );
        }
      }

      for (const edge of batch.edges) {
        const endpoints = await client.query(
          `SELECT graph_node_id, node_type, external_key FROM graph_nodes
            WHERE event_id=$1 AND mode=$2 AND active=true
              AND ((node_type=$3 AND external_key=$4) OR (node_type=$5 AND external_key=$6))`,
          [eventId, mode, edge.from.nodeType, edge.from.externalKey, edge.to.nodeType, edge.to.externalKey],
        );
        const from = endpoints.rows.find(row => row.node_type === edge.from.nodeType && row.external_key === edge.from.externalKey);
        const to = endpoints.rows.find(row => row.node_type === edge.to.nodeType && row.external_key === edge.to.externalKey);
        if (!from || !to) { rejectedCount += 1; continue; }
        const hash = canonicalHash({ from: from.graph_node_id, to: to.graph_node_id, state: edge.state, geometry: edge.geometryGeojson ?? null, flags: edge.qualityFlags ?? [], active: edge.active ?? true, validUntil: edge.validUntil ?? null });
        const result = await client.query(
          `INSERT INTO graph_edges (
             event_id, mode, edge_type, external_key, from_node_id, to_node_id, directed,
             owner_agency_id, source_id, authority_uri, data_classification, geometry_geojson,
             current_state, quality_flags, state_hash, valid_from, valid_until, active
           ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14,$15,$16,$17,$18)
           ON CONFLICT (event_id, mode, edge_type, external_key) DO UPDATE SET
             from_node_id=EXCLUDED.from_node_id, to_node_id=EXCLUDED.to_node_id, directed=EXCLUDED.directed,
             owner_agency_id=EXCLUDED.owner_agency_id, source_id=EXCLUDED.source_id,
             authority_uri=EXCLUDED.authority_uri, data_classification=EXCLUDED.data_classification,
             geometry_geojson=EXCLUDED.geometry_geojson, current_state=EXCLUDED.current_state,
             quality_flags=EXCLUDED.quality_flags, state_hash=EXCLUDED.state_hash,
             valid_from=EXCLUDED.valid_from, valid_until=EXCLUDED.valid_until, active=EXCLUDED.active
           WHERE graph_edges.state_hash IS DISTINCT FROM EXCLUDED.state_hash
           RETURNING graph_edge_id`,
          [eventId, mode, edge.edgeType, edge.externalKey, from.graph_node_id, to.graph_node_id,
           edge.directed ?? true, edge.ownerAgencyId ?? null, sourceId, edge.authorityUri ?? null,
           edge.dataClassification, edge.geometryGeojson ? JSON.stringify(edge.geometryGeojson) : null,
           JSON.stringify(edge.state), edge.qualityFlags ?? [], hash, edge.validFrom, edge.validUntil ?? null, edge.active ?? true],
        );
        const edgeId = result.rowCount ? result.rows[0].graph_edge_id : (await client.query(
          'SELECT graph_edge_id FROM graph_edges WHERE event_id=$1 AND mode=$2 AND edge_type=$3 AND external_key=$4',
          [eventId, mode, edge.edgeType, edge.externalKey],
        )).rows[0].graph_edge_id;
        if (result.rowCount) edgeCount += 1; else unchangedCount += 1;
        for (const evidenceId of edge.evidenceIds ?? []) {
          await client.query(
            `INSERT INTO graph_edge_evidence (graph_edge_id, evidence_id, relationship)
             SELECT $1, evidence_id, 'observed_by' FROM evidence_events WHERE evidence_id=$2 AND event_id=$3
             ON CONFLICT DO NOTHING`,
            [edgeId, evidenceId, eventId],
          );
        }
      }

      const status = rejectedCount ? 'partial' : 'succeeded';
      await client.query(
        `UPDATE graph_ingestion_batches SET status=$1,node_count=$2,edge_count=$3,unchanged_count=$4,
           rejected_count=$5,completed_at=now() WHERE graph_batch_id=$6`,
        [status, nodeCount, edgeCount, unchangedCount, rejectedCount, batchId],
      );
      await client.query(
        `INSERT INTO outbox_events (event_type, aggregate_type, aggregate_id, aggregate_version, mode, payload)
         VALUES ('graph.batch.ingested','graph_batch',$1,1,$2,$3::jsonb)`,
        [batchId, mode, JSON.stringify({ eventId, sourceId, nodeCount, edgeCount, unchangedCount, rejectedCount })],
      );
      return { batchId, status, nodeCount, edgeCount, unchangedCount, rejectedCount, requestId };
    });
  }

  async snapshot(eventId: string, view: GraphView, asOf: string, context: GraphQueryContext): Promise<GraphSnapshot> {
    const nodeResult = await getDatabasePool().query(
      `SELECT * FROM graph_nodes WHERE event_id=$1 AND mode=$2 AND active=true
         AND node_type=ANY($3::text[]) AND valid_from <= $4
         AND (valid_until IS NULL OR valid_until > $4) ORDER BY node_type,label`,
      [eventId, context.mode, viewTypes(view), asOf],
    );
    const nodes = nodeResult.rows.map(mapNode);
    const ids = nodes.map(node => node.nodeId);
    const edgeResult = ids.length ? await getDatabasePool().query(
      `SELECT * FROM graph_edges WHERE event_id=$1 AND mode=$2 AND active=true
         AND from_node_id=ANY($3::uuid[]) AND to_node_id=ANY($3::uuid[])
         AND valid_from <= $4 AND (valid_until IS NULL OR valid_until > $4)
         ORDER BY edge_type,external_key`,
      [eventId, context.mode, ids, asOf],
    ) : { rows: [] };
    return { eventId, mode: context.mode, view, asOf, nodes, edges: edgeResult.rows.map(mapEdge), generatedAt: new Date().toISOString() };
  }

  async neighborhood(eventId: string, nodeId: string, depth: number, context: GraphQueryContext): Promise<GraphSnapshot> {
    const result = await getDatabasePool().query(
      `WITH RECURSIVE walk(node_id,depth,path) AS (
         SELECT $3::uuid,0,ARRAY[$3::uuid]
         UNION ALL
         SELECT CASE WHEN e.from_node_id=w.node_id THEN e.to_node_id ELSE e.from_node_id END,
                w.depth+1, w.path || CASE WHEN e.from_node_id=w.node_id THEN e.to_node_id ELSE e.from_node_id END
         FROM walk w JOIN graph_edges e ON (e.from_node_id=w.node_id OR e.to_node_id=w.node_id)
         WHERE e.event_id=$1 AND e.mode=$2 AND e.active=true AND w.depth < $4
           AND NOT (CASE WHEN e.from_node_id=w.node_id THEN e.to_node_id ELSE e.from_node_id END)=ANY(w.path)
       ) SELECT DISTINCT node_id FROM walk`,
      [eventId, context.mode, nodeId, Math.min(depth, 4)],
    );
    const ids = result.rows.map(row => row.node_id);
    const nodes = ids.length ? (await getDatabasePool().query('SELECT * FROM graph_nodes WHERE graph_node_id=ANY($1::uuid[])', [ids])).rows.map(mapNode) : [];
    const edges = ids.length ? (await getDatabasePool().query(
      'SELECT * FROM graph_edges WHERE event_id=$1 AND mode=$2 AND active=true AND from_node_id=ANY($3::uuid[]) AND to_node_id=ANY($3::uuid[])',
      [eventId, context.mode, ids],
    )).rows.map(mapEdge) : [];
    return { eventId, mode: context.mode, view: 'mobility', asOf: new Date().toISOString(), nodes, edges, generatedAt: new Date().toISOString() };
  }

  async stateHistory(kind: GraphEntityKind, entityId: string, limit: number, context: GraphQueryContext): Promise<GraphStateChange[]> {
    const result = await getDatabasePool().query(
      `SELECT * FROM graph_state_changes WHERE entity_kind=$1 AND entity_id=$2 AND mode=$3
       ORDER BY occurred_at DESC LIMIT $4`, [kind, entityId, context.mode, Math.min(limit, 500)],
    );
    return result.rows.map(row => ({
      stateChangeId: row.graph_state_change_id, entityKind: row.entity_kind, entityId: row.entity_id,
      entityType: row.entity_type, changeType: row.change_type, previousVersion: row.previous_version,
      newVersion: row.new_version, previousState: row.previous_state, newState: row.new_state,
      qualityFlags: row.quality_flags ?? [], sourceId: row.source_id ?? null, evidenceIds: row.evidence_ids ?? [],
      requestId: row.request_id ?? null, occurredAt: iso(row.occurred_at),
    }));
  }

  async decisionLineage(recommendationId: string, context: GraphQueryContext): Promise<Record<string, unknown>> {
    const result = await getDatabasePool().query(
      `SELECT jsonb_build_object(
         'recommendation',to_jsonb(r),'incident',to_jsonb(i),
         'evidence',COALESCE((SELECT jsonb_agg(to_jsonb(ee)) FROM recommendation_evidence re JOIN evidence_events ee USING(evidence_id) WHERE re.recommendation_id=r.recommendation_id),'[]'::jsonb),
         'decisions',COALESCE((SELECT jsonb_agg(to_jsonb(d) ORDER BY d.decided_at) FROM decisions d WHERE d.recommendation_id=r.recommendation_id),'[]'::jsonb),
         'commitments',COALESCE((SELECT jsonb_agg(to_jsonb(c)) FROM commitments c WHERE c.recommendation_id=r.recommendation_id),'[]'::jsonb),
         'verifications',COALESCE((SELECT jsonb_agg(to_jsonb(ec)) FROM execution_confirmations ec JOIN commitments c USING(commitment_id) WHERE c.recommendation_id=r.recommendation_id),'[]'::jsonb)
       ) AS lineage FROM recommendations r JOIN incidents i USING(incident_id)
       WHERE r.recommendation_id=$1 AND r.mode=$2`, [recommendationId, context.mode],
    );
    if (!result.rowCount) throw new OperationalError(404, 'RECOMMENDATION_NOT_FOUND', 'Recommendation lineage not found');
    return result.rows[0].lineage;
  }

  async agencyCoordination(eventId: string, context: GraphQueryContext): Promise<Record<string, unknown>> {
    const result = await getDatabasePool().query(
      `SELECT c.commitment_id,c.state,c.requested_outcome,c.due_at,c.blocker,
              owner.agency_id AS owner_agency_id,owner.name AS owner_agency_name,
              requester.agency_id AS requester_agency_id,requester.name AS requester_agency_name,
              r.recommendation_id,r.priority,i.incident_id,i.title AS incident_title
         FROM commitments c JOIN agencies owner ON owner.agency_id=c.owner_agency_id
         JOIN decisions d ON d.decision_id=c.decision_id LEFT JOIN agencies requester ON requester.agency_id=d.actor_agency_id
         JOIN recommendations r ON r.recommendation_id=c.recommendation_id JOIN incidents i ON i.incident_id=c.incident_id
        WHERE i.event_id=$1 AND c.mode=$2 AND c.state NOT IN ('verified','failed','expired','cancelled')
        ORDER BY c.due_at NULLS LAST,r.priority,owner.name`, [eventId, context.mode],
    );
    return { eventId, mode: context.mode, commitments: result.rows, generatedAt: new Date().toISOString() };
  }
}
