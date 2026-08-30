import request from 'supertest';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp } from '../app.js';
import { ReviewOperationalRepository } from '../operational/reviewRepository.js';
import type { GraphRepository } from './repository.js';

const eventId = '22222222-2222-4222-8222-222222222222';
const sourceId = '33333333-3333-4333-8333-333333333333';

function graphRepository(): GraphRepository {
  return {
    ingestBatch: vi.fn(async (_eventId, _sourceId, batch, _context, _key, requestId) => ({
      batchId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', status: 'succeeded',
      nodeCount: batch.nodes.length, edgeCount: batch.edges.length, unchangedCount: 0,
      rejectedCount: 0, requestId,
    })),
    snapshot: vi.fn(async (event, view, asOf, context) => ({ eventId: event, mode: context.mode, view, asOf, nodes: [], edges: [], generatedAt: new Date().toISOString() })),
    neighborhood: vi.fn(async (event, _node, _depth, context) => ({ eventId: event, mode: context.mode, view: 'mobility', asOf: new Date().toISOString(), nodes: [], edges: [], generatedAt: new Date().toISOString() })),
    stateHistory: vi.fn(async () => []),
    decisionLineage: vi.fn(async recommendationId => ({ recommendationId, evidence: [], decisions: [], commitments: [] })),
    agencyCoordination: vi.fn(async event => ({ eventId: event, commitments: [] })),
  };
}

describe('Nexus temporal graph API', () => {
  beforeEach(() => {
    process.env.NEXUS_AUTH_MODE = 'review';
    process.env.NODE_ENV = 'test';
  });

  it('projects decision lineage from operational records when graph storage is not configured', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    const response = await request(app).get(`/api/v1/events/${eventId}/graph?mode=live&view=decision_lineage`).expect(200);
    const types = response.body.data.nodes.map((node: { nodeType: string }) => node.nodeType);
    expect(types).toEqual(expect.arrayContaining(['incident', 'recommendation', 'evidence', 'finding']));
    expect(response.body.data.edges.length).toBeGreaterThan(0);
  });

  it('fails ingest when persistent graph storage is not configured', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false });
    const response = await request(app)
      .post(`/api/v1/events/${eventId}/graph/sources/${sourceId}/batches`)
      .set('Idempotency-Key', 'graph-ingest-missing-store')
      .send({ mode: 'live', schemaVersion: '1.0.0', nodes: [{ nodeType: 'parking_lot', externalKey: 'lot-west', label: 'West Campus Lot', dataClassification: 'live', state: {}, validFrom: new Date().toISOString() }], edges: [] })
      .expect(503);
    expect(response.body.error.code).toBe('GRAPH_STORAGE_NOT_CONFIGURED');
  });

  it('returns a mode-bound graph snapshot for an authorized operator', async () => {
    const repository = graphRepository();
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false, graphRepository: repository });
    const response = await request(app).get(`/api/v1/events/${eventId}/graph?mode=live&view=mobility`).expect(200);
    expect(response.body.data).toEqual(expect.objectContaining({ eventId, mode: 'live', view: 'mobility', nodes: [], edges: [] }));
    expect(repository.snapshot).toHaveBeenCalledOnce();
  });

  it('requires an idempotency key for authoritative graph ingestion', async () => {
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false, graphRepository: graphRepository() });
    await request(app)
      .post(`/api/v1/events/${eventId}/graph/sources/${sourceId}/batches`)
      .send({ mode: 'live', schemaVersion: '1.0.0', nodes: [{ nodeType: 'parking_lot', externalKey: 'lot-west', label: 'West Campus Lot', dataClassification: 'live', state: { occupancyPercent: 95 }, validFrom: new Date().toISOString() }], edges: [] })
      .expect(422);
  });

  it('accepts a validated authoritative node-and-edge batch with request correlation', async () => {
    const repository = graphRepository();
    const app = createApp(new ReviewOperationalRepository(), { serveStatic: false, graphRepository: repository });
    const now = new Date().toISOString();
    const response = await request(app)
      .post(`/api/v1/events/${eventId}/graph/sources/${sourceId}/batches`)
      .set('Idempotency-Key', 'graph-ingestion-001')
      .set('X-Request-ID', 'request-graph-001')
      .send({
        mode: 'live', schemaVersion: '1.0.0',
        nodes: [
          { nodeType: 'parking_lot', externalKey: 'lot-west', label: 'West Campus Lot', dataClassification: 'live', state: { occupancyPercent: 95 }, validFrom: now },
          { nodeType: 'road_segment', externalKey: 'wire-road', label: 'Wire Road', dataClassification: 'near_real_time', state: { speedMph: 12 }, validFrom: now },
        ],
        edges: [{ edgeType: 'feeds_traffic_into', externalKey: 'lot-west:wire-road', from: { nodeType: 'parking_lot', externalKey: 'lot-west' }, to: { nodeType: 'road_segment', externalKey: 'wire-road' }, dataClassification: 'operational', state: { active: true }, validFrom: now }],
      })
      .expect(202);
    expect(response.body.data).toEqual(expect.objectContaining({ nodeCount: 2, edgeCount: 1, rejectedCount: 0, requestId: 'request-graph-001' }));
    expect(repository.ingestBatch).toHaveBeenCalledOnce();
  });
});
