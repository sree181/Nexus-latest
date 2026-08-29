import { randomUUID } from 'node:crypto';
import { Router, type Request, type Response } from 'express';
import { z } from 'zod';
import { authenticateRequest, requireScope } from './auth.js';
import type { OperationalMode } from './domain.js';
import { OperationalError, validation } from './errors.js';
import type { OperationalRepository } from './repository.js';
import { ConnectorError } from '../connectors/types.js';
import type { ConnectorService } from '../connectors/service.js';
import { createGraphRouter } from '../graph/routes.js';
import type { GraphRepository } from '../graph/repository.js';
import { cityReferenceLayers, loadCityReferenceLayer, referenceLayerByCode } from '../reference/cityLayers.js';

const decisionSchema = z.object({
  action: z.enum(['approve', 'reject', 'request_revision', 'delegate', 'escalate', 'acknowledge', 'withdraw']),
  recommendationVersion: z.number().int().positive(),
  expectedState: z.enum([
    'draft', 'awaiting_acknowledgement', 'awaiting_approval', 'approved', 'rejected',
    'revision_requested', 'delegated', 'escalated', 'expired', 'superseded',
  ]),
  evidenceSnapshotHash: z.string().min(8).max(256),
  reasonCode: z.string().min(2).max(100),
  comment: z.string().max(2_000).optional(),
  delegateToPrincipalId: z.string().uuid().optional(),
  escalateToRoleCode: z.string().max(100).optional(),
  confirmationTextHash: z.string().max(256).optional(),
});

const commitmentTransitionSchema = z.object({
  expectedVersion: z.number().int().positive(),
  targetState: z.enum(['requested', 'acknowledged', 'approved', 'executing', 'blocked', 'verified', 'failed', 'expired', 'cancelled']),
  reasonCode: z.string().min(2).max(100),
  comment: z.string().max(2_000).optional(),
  evidenceIds: z.array(z.string().uuid()).max(50).optional(),
});

const openOperatingWindowSchema = z.object({
  packCode: z.string().regex(/^[a-z0-9_]{3,60}$/),
  name: z.string().min(3).max(200),
  locationName: z.string().min(2).max(200),
  mode: z.enum(['live', 'training', 'replay']).default('live'),
  startsAt: z.string().datetime().optional(),
  endsAt: z.string().datetime().nullable().optional(),
});

function requirePrincipal(req: Request) {
  if (!req.principal) throw new OperationalError(401, 'AUTHENTICATION_REQUIRED', 'Authentication is required');
  return req.principal;
}

function idempotencyKey(req: Request): string {
  const value = req.header('idempotency-key')?.trim();
  if (!value || value.length < 8 || value.length > 200) {
    throw validation('Idempotency-Key header is required and must be 8–200 characters');
  }
  return value;
}

function asOperationalConnectorError(error: ConnectorError): OperationalError {
  const status = error.category === 'permission_required' ? 403
    : error.category === 'configuration_required' ? 503
      : error.category === 'rate_limited' ? 429
        : error.category === 'invalid_payload' ? 502
          : 503;
  return new OperationalError(status, `CONNECTOR_${error.category.toUpperCase()}`, error.message);
}

export function createOperationalRouter(repository: OperationalRepository, connectorService?: ConnectorService, graphRepository?: GraphRepository): Router {
  const router = Router();
  router.use((req, _res, next) => {
    req.requestId = req.header('x-request-id')?.trim() || randomUUID();
    next();
  });
  router.get('/auth/config', (_req, res) => {
    const issuer = (process.env.NEXUS_OIDC_ISSUER ?? process.env.OIDC_ISSUER ?? '').trim() || null;
    const clientId = (process.env.NEXUS_OIDC_CLIENT_ID ?? process.env.OIDC_CLIENT_ID ?? '').trim() || null;
    const audience = (process.env.NEXUS_OIDC_AUDIENCE ?? process.env.OIDC_AUDIENCE ?? '').trim() || null;
    const authMode = process.env.NEXUS_AUTH_MODE || (process.env.NODE_ENV === 'production' ? 'oidc_jwt' : 'review');
    res.json({
      data: {
        configured: Boolean(issuer && clientId && audience),
        loginRequired: authMode !== 'review',
        issuer,
        clientId,
        audience,
        scopes: 'openid profile email',
      },
      requestId: _req.requestId,
    });
  });
  router.use(authenticateRequest);
  router.use((_req, res, next) => {
    res.setHeader('Cache-Control', 'no-store');
    next();
  });
  router.use(createGraphRouter(graphRepository));

  router.get('/me', (req, res) => {
    const principal = requirePrincipal(req);
    res.json({ data: principal, requestId: req.requestId });
  });

  router.get('/system/status', async (req, res, next) => {
    try {
      const status = await repository.systemStatus();
      res.status(status.status === 'major_degradation' ? 503 : 200).json({ data: status, requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.get('/connectors', requireScope('connector:read'), async (req, res, next) => {
    try {
      if (!connectorService) throw new OperationalError(503, 'CONNECTOR_STORAGE_NOT_CONFIGURED', 'Persistent connector storage is not configured');
      res.json({ data: await connectorService.statuses(), requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.post('/events/:eventId/connectors/:connectorCode/runs', requireScope('connector:run'), async (req, res, next) => {
    try {
      if (!connectorService) throw new OperationalError(503, 'CONNECTOR_STORAGE_NOT_CONFIGURED', 'Persistent connector storage is not configured');
      const eventId = z.string().uuid().parse(req.params.eventId);
      const connectorCode = z.string().regex(/^[a-z0-9-]{3,100}$/).parse(req.params.connectorCode);
      const statuses = await connectorService.run(connectorCode, eventId, 'manual', idempotencyKey(req));
      res.json({ data: statuses, requestId: req.requestId });
    } catch (error) {
      next(error instanceof ConnectorError ? asOperationalConnectorError(error) : error);
    }
  });

  router.get('/reference-layers', requireScope('event:read'), (req, res) => {
    res.json({ data: cityReferenceLayers, requestId: req.requestId });
  });

  router.get('/reference-layers/:layerCode', requireScope('event:read'), async (req, res, next) => {
    try {
      const definition = referenceLayerByCode(z.string().regex(/^[a-z0-9-]{3,60}$/).parse(req.params.layerCode));
      if (!definition) throw new OperationalError(404, 'REFERENCE_LAYER_NOT_FOUND', 'No such City reference layer');
      const layer = await loadCityReferenceLayer(definition);
      res.json({ data: layer, requestId: req.requestId });
    } catch (error) {
      next(error instanceof ConnectorError ? asOperationalConnectorError(error) : error);
    }
  });

  router.get('/scenario-packs', requireScope('event:read'), async (req, res, next) => {
    try {
      res.json({ data: await repository.scenarioPacks(), requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.post('/events', requireScope('event:manage'), async (req, res, next) => {
    try {
      const event = await repository.openOperatingWindow(
        openOperatingWindowSchema.parse(req.body),
        requirePrincipal(req),
        idempotencyKey(req),
        req.requestId || randomUUID(),
      );
      res.status(201).json({ data: event, requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.post('/events/:eventId/close', requireScope('event:manage'), async (req, res, next) => {
    try {
      const event = await repository.closeOperatingWindow(
        z.string().uuid().parse(req.params.eventId),
        requirePrincipal(req),
        req.requestId || randomUUID(),
      );
      res.json({ data: event, requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.get('/events/active', requireScope('event:read'), async (req, res, next) => {
    try {
      const mode = z.enum(['live', 'training', 'replay']).parse(req.query.mode || 'live') as OperationalMode;
      const event = await repository.activeEvent(mode);
      if (!event) {
        res.status(404).json({ error: { code: 'NO_ACTIVE_EVENT', message: `No active ${mode} event` }, requestId: req.requestId });
        return;
      }
      res.json({ data: event, requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.get('/events/:eventId/snapshot', requireScope('event:read'), async (req, res, next) => {
    try {
      const snapshot = await repository.snapshot(z.string().uuid().parse(req.params.eventId), requirePrincipal(req));
      res.json({ data: snapshot, requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.get('/recommendations/:recommendationId', requireScope('recommendation:read'), async (req, res, next) => {
    try {
      const recommendation = await repository.recommendation(
        z.string().uuid().parse(req.params.recommendationId),
        requirePrincipal(req),
      );
      res.json({ data: recommendation, requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.post('/recommendations/:recommendationId/decisions', async (req, res, next) => {
    try {
      const result = await repository.decide(
        z.string().uuid().parse(req.params.recommendationId),
        decisionSchema.parse(req.body),
        requirePrincipal(req),
        idempotencyKey(req),
        req.requestId || randomUUID(),
      );
      res.json({ data: result, requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.post('/commitments/:commitmentId/transitions', async (req, res, next) => {
    try {
      const commitment = await repository.transitionCommitment(
        z.string().uuid().parse(req.params.commitmentId),
        commitmentTransitionSchema.parse(req.body),
        requirePrincipal(req),
        idempotencyKey(req),
        req.requestId || randomUUID(),
      );
      res.json({ data: commitment, requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.get('/events/:eventId/audit', requireScope('audit:read'), async (req, res, next) => {
    try {
      const records = await repository.audit(
        z.string().uuid().parse(req.params.eventId),
        z.coerce.number().int().nonnegative().parse(req.query.after || 0),
        z.coerce.number().int().positive().max(500).parse(req.query.limit || 100),
        requirePrincipal(req),
      );
      res.json({ data: records, requestId: req.requestId });
    } catch (error) {
      next(error);
    }
  });

  router.get('/events/:eventId/stream', requireScope('event:read'), async (req, res, next) => {
    try {
      const eventId = z.string().uuid().parse(req.params.eventId);
      const principal = requirePrincipal(req);
      let cursor = z.coerce.number().int().nonnegative().parse(req.header('last-event-id') || req.query.after || 0);

      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache, no-transform');
      res.setHeader('Connection', 'keep-alive');
      res.setHeader('X-Accel-Buffering', 'no');
      res.flushHeaders();

      let closed = false;
      req.on('close', () => { closed = true; });

      const publish = async () => {
        const events = await repository.stream(eventId, cursor, principal);
        for (const event of events) {
          cursor += 1;
          res.write(`id: ${cursor}\n`);
          res.write(`event: ${event.eventType}\n`);
          res.write(`data: ${JSON.stringify(event)}\n\n`);
        }
      };

      await publish();
      const interval = setInterval(async () => {
        if (closed) {
          clearInterval(interval);
          return;
        }
        try {
          await publish();
          res.write(`: heartbeat ${Date.now()}\n\n`);
        } catch (error) {
          res.write(`event: stream_error\ndata: ${JSON.stringify({ message: 'Realtime refresh failed' })}\n\n`);
        }
      }, 5_000);
    } catch (error) {
      if (!res.headersSent) next(error);
    }
  });

  return router;
}
