import cors from 'cors';
import express, { type Express } from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ZodError } from 'zod';
import { OperationalError } from './operational/errors.js';
import type { OperationalRepository } from './operational/repository.js';
import { createOperationalRouter } from './operational/routes.js';
import type { ConnectorService } from './connectors/service.js';
import type { GraphRepository } from './graph/repository.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export function createApp(
  repository: OperationalRepository,
  options: { serveStatic?: boolean; connectorService?: ConnectorService; graphRepository?: GraphRepository } = {},
): Express {
  const app = express();
  app.disable('x-powered-by');

  const allowedOrigins = (process.env.NEXUS_ALLOWED_ORIGINS || process.env.ALLOWED_ORIGINS || '')
    .split(',')
    .map(value => value.trim())
    .filter(Boolean);

  app.use(cors({ credentials: true, origin: allowedOrigins.length > 0 ? allowedOrigins : false }));
  app.use(express.json({ limit: '256kb', strict: true }));
  app.use((_req, res, next) => {
    let issuerOrigin = '';
    try {
      issuerOrigin = process.env.NEXUS_OIDC_ISSUER ? new URL(process.env.NEXUS_OIDC_ISSUER).origin : '';
    } catch {
      issuerOrigin = '';
    }
    const connectSrc = ["'self'", 'https://server.arcgisonline.com', issuerOrigin].filter(Boolean).join(' ');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'same-origin');
    res.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
    res.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
    res.setHeader(
      'Content-Security-Policy',
      `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https: blob:; connect-src ${connectSrc}; worker-src 'self' blob:; font-src 'self' https: data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'`,
    );
    next();
  });

  app.get('/api/health', async (_req, res) => {
    const status = await repository.systemStatus();
    res.status(status.status === 'major_degradation' ? 503 : 200).json({
      status: status.status,
      database: status.database,
      checkedAt: status.checkedAt,
    });
  });
  app.use('/api/v1', createOperationalRouter(repository, options.connectorService, options.graphRepository));

  if (options.serveStatic !== false) {
    const distPath = path.resolve(__dirname, '..', 'dist');
    app.use(express.static(distPath));
    app.use((req, res, next) => {
      if (req.method === 'GET' && !req.path.startsWith('/api')) {
        res.sendFile(path.join(distPath, 'index.html'));
      } else {
        next();
      }
    });
  }

  app.use((req, res) => {
    res.status(404).json({ error: { code: 'ROUTE_NOT_FOUND', message: `No route for ${req.method} ${req.path}` } });
  });

  app.use((error: unknown, req: express.Request, res: express.Response, _next: express.NextFunction) => {
    if (error instanceof ZodError) {
      res.status(422).json({
        error: {
          code: 'VALIDATION_FAILED',
          message: 'Request validation failed',
          details: error.flatten(),
        },
        requestId: req.requestId,
      });
      return;
    }

    const operationalError = error instanceof OperationalError
      ? error
      : new OperationalError(500, 'INTERNAL_ERROR', 'An unexpected operational service error occurred');

    if (operationalError.status >= 500) console.error('[operational-api]', error);
    res.status(operationalError.status).json({
      error: {
        code: operationalError.code,
        message: operationalError.message,
        details: operationalError.details,
      },
      requestId: req.requestId,
    });
  });

  return app;
}
