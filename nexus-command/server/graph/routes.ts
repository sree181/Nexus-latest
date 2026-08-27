import { Router, type Request } from 'express';
import { z } from 'zod';
import { requireScope } from '../operational/auth.js';
import type { OperationalMode } from '../operational/domain.js';
import { OperationalError, validation } from '../operational/errors.js';
import type { GraphRepository } from './repository.js';

const modeSchema = z.enum(['live', 'training', 'replay']);
const classificationSchema = z.enum(['live', 'near_real_time', 'reference', 'operational', 'restricted']);
const geometrySchema = z.record(z.string(), z.unknown());
const typeIdentifier = z.string().regex(/^[a-zA-Z][a-zA-Z0-9_-]{1,79}$/);
const externalKey = z.string().min(1).max(300).regex(/^[^\u0000-\u001F\u007F]+$/);
const stateSchema = z.record(z.string(), z.unknown());

const endpointSchema = z.object({ nodeType: typeIdentifier, externalKey });
const nodeSchema = z.object({
  nodeType: typeIdentifier, externalKey, label: z.string().min(1).max(300),
  ownerAgencyId: z.string().uuid().optional(), authorityUri: z.string().url().max(2_000).optional(),
  dataClassification: classificationSchema, geometryGeojson: geometrySchema.nullable().optional(),
  state: stateSchema, qualityFlags: z.array(z.string().max(100)).max(50).optional(),
  validFrom: z.string().datetime(), validUntil: z.string().datetime().nullable().optional(),
  active: z.boolean().optional(), evidenceIds: z.array(z.string().uuid()).max(100).optional(),
});
const edgeSchema = z.object({
  edgeType: typeIdentifier, externalKey, from: endpointSchema, to: endpointSchema,
  directed: z.boolean().optional(), ownerAgencyId: z.string().uuid().optional(),
  authorityUri: z.string().url().max(2_000).optional(), dataClassification: classificationSchema,
  geometryGeojson: geometrySchema.nullable().optional(), state: stateSchema,
  qualityFlags: z.array(z.string().max(100)).max(50).optional(), validFrom: z.string().datetime(),
  validUntil: z.string().datetime().nullable().optional(), active: z.boolean().optional(),
  evidenceIds: z.array(z.string().uuid()).max(100).optional(),
}).refine(edge => !(edge.from.nodeType === edge.to.nodeType && edge.from.externalKey === edge.to.externalKey), {
  message: 'Graph edge endpoints must be different',
});
const batchSchema = z.object({
  mode: modeSchema, schemaVersion: z.string().min(1).max(100),
  nodes: z.array(nodeSchema).max(1_000), edges: z.array(edgeSchema).max(2_000),
}).refine(batch => batch.nodes.length + batch.edges.length > 0, { message: 'Graph batch must contain nodes or edges' });

function principal(req: Request) {
  if (!req.principal) throw new OperationalError(401, 'AUTHENTICATION_REQUIRED', 'Authentication is required');
  return req.principal;
}

function idempotencyKey(req: Request): string {
  const value = req.header('idempotency-key')?.trim();
  if (!value || value.length < 8 || value.length > 200) throw validation('Idempotency-Key header is required and must be 8–200 characters');
  return value;
}

function context(req: Request, mode: OperationalMode) {
  const actor = principal(req);
  if (!actor.modes.includes(mode)) throw new OperationalError(403, 'MODE_AUTHORITY_REQUIRED', `Operator is not authorized for ${mode} mode`);
  return { principal: actor, mode };
}

export function createGraphRouter(repository?: GraphRepository): Router {
  const router = Router();
  const required = (): GraphRepository => {
    if (!repository) throw new OperationalError(503, 'GRAPH_STORAGE_NOT_CONFIGURED', 'Persistent graph storage is not configured');
    return repository;
  };

  router.post('/events/:eventId/graph/sources/:sourceId/batches', requireScope('graph:ingest'), async (req, res, next) => {
    try {
      const eventId = z.string().uuid().parse(req.params.eventId);
      const sourceId = z.string().uuid().parse(req.params.sourceId);
      const body = batchSchema.parse(req.body);
      const data = await required().ingestBatch(eventId, sourceId, body, context(req, body.mode), idempotencyKey(req), req.requestId!);
      res.status(202).json({ data, requestId: req.requestId });
    } catch (error) { next(error); }
  });

  router.get('/events/:eventId/graph', requireScope('graph:read'), async (req, res, next) => {
    try {
      const eventId = z.string().uuid().parse(req.params.eventId);
      const mode = modeSchema.parse(req.query.mode ?? 'live');
      const view = z.enum(['mobility', 'decision_lineage', 'agency_coordination']).parse(req.query.view ?? 'mobility');
      const asOf = z.string().datetime().parse(req.query.asOf ?? new Date().toISOString());
      res.json({ data: await required().snapshot(eventId, view, asOf, context(req, mode)), requestId: req.requestId });
    } catch (error) { next(error); }
  });

  router.get('/events/:eventId/graph/nodes/:nodeId/neighborhood', requireScope('graph:read'), async (req, res, next) => {
    try {
      const eventId = z.string().uuid().parse(req.params.eventId);
      const nodeId = z.string().uuid().parse(req.params.nodeId);
      const mode = modeSchema.parse(req.query.mode ?? 'live');
      const depth = z.coerce.number().int().min(1).max(4).parse(req.query.depth ?? 1);
      res.json({ data: await required().neighborhood(eventId, nodeId, depth, context(req, mode)), requestId: req.requestId });
    } catch (error) { next(error); }
  });

  router.get('/graph/entities/:kind/:entityId/history', requireScope('graph:read'), async (req, res, next) => {
    try {
      const kind = z.enum(['node', 'edge']).parse(req.params.kind);
      const entityId = z.string().uuid().parse(req.params.entityId);
      const mode = modeSchema.parse(req.query.mode ?? 'live');
      const limit = z.coerce.number().int().min(1).max(500).parse(req.query.limit ?? 100);
      res.json({ data: await required().stateHistory(kind, entityId, limit, context(req, mode)), requestId: req.requestId });
    } catch (error) { next(error); }
  });

  router.get('/graph/decision-lineage/:recommendationId', requireScope('graph:read'), async (req, res, next) => {
    try {
      const recommendationId = z.string().uuid().parse(req.params.recommendationId);
      const mode = modeSchema.parse(req.query.mode ?? 'live');
      res.json({ data: await required().decisionLineage(recommendationId, context(req, mode)), requestId: req.requestId });
    } catch (error) { next(error); }
  });

  router.get('/events/:eventId/graph/agency-coordination', requireScope('graph:read'), async (req, res, next) => {
    try {
      const eventId = z.string().uuid().parse(req.params.eventId);
      const mode = modeSchema.parse(req.query.mode ?? 'live');
      res.json({ data: await required().agencyCoordination(eventId, context(req, mode)), requestId: req.requestId });
    } catch (error) { next(error); }
  });

  return router;
}
