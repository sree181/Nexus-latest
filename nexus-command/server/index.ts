/**
 * Nexus Coordinate — Simulation-Free Operational Server
 *
 * The live application exposes evidence, incidents, recommendations, human
 * decisions, agency commitments, audit records, and realtime updates. Legacy
 * simulation and MARL playback routes are intentionally not mounted.
 *
 * One image serves two roles. `NEXUS_SERVICE_ROLE` selects which, because a platform that
 * always runs the image default will otherwise start a second API and no worker at all,
 * which looks healthy while ingestion silently stops between deploys.
 */
import './loadEnv.js';
import express from 'express';
import { createApp } from './app.js';
import { createOperationalRepository } from './operational/repositoryFactory.js';
import { hasDatabaseConfiguration } from './operational/database.js';
import { authoritativeConnectors } from './connectors/registry.js';
import { PostgresConnectorStore } from './connectors/postgresStore.js';
import { ingestConfiguredFeeds } from './connectors/scheduledIngest.js';
import { ConnectorService } from './connectors/service.js';
import { startConnectorWorker } from './connectors/workerLoop.js';
import { PostgresGraphRepository } from './graph/postgresRepository.js';
import { resolveServiceRole } from './serviceRole.js';
import { atlasAiConfig } from './operational/agents/atlas/config.js';

const port = Number(process.env.PORT || process.env.AGENT_PORT || 4002);
const role = resolveServiceRole(process.env.NEXUS_SERVICE_ROLE);
const atlas = atlasAiConfig();
if (atlas.enabled) {
  console.info('[desk-agent] ATLAS and AQUA enabled', { model: atlas.model, host: atlas.baseUrl });
} else {
  console.info('[desk-agent] Rule assessor only (set ATLAS_AI_ENABLED and GROQ_API_KEY to enable ATLAS and AQUA)');
}

if (role === 'connector-worker') {
  if (!hasDatabaseConfiguration()) {
    throw new Error('DATABASE_URL is required for the Nexus connector worker');
  }
  const worker = startConnectorWorker();

  // The platform health-probes every service. Answering with the loop's own liveness is more
  // useful than not listening at all, and it keeps the worker from being failed on deploy.
  const health = express();
  health.get('/api/health', (_req, res) => {
    const status = worker.status();
    res.status(status.alive ? 200 : 503).json({ status: status.alive ? 'operational' : 'stalled', ...status });
  });
  health.listen(port, '0.0.0.0', () => {
    console.log(`[NEXUS] Connector worker health endpoint listening on 0.0.0.0:${port}`);
  });

  const shutdown = (signal: string) => {
    console.info(`[connector-worker] Received ${signal}; stopping after the current ingestion cycle`);
    void worker.stop();
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
} else {
  const repository = createOperationalRepository();
  const configuredConnectors = new Set(authoritativeConnectors.filter(connector => connector.isConfigured()).map(connector => connector.definition.code));
  const connectorService = hasDatabaseConfiguration()
    ? new ConnectorService(authoritativeConnectors, new PostgresConnectorStore(configuredConnectors))
    : undefined;
  if (connectorService) await connectorService.initialize();
  const graphRepository = hasDatabaseConfiguration() ? new PostgresGraphRepository() : undefined;
  const app = createApp(repository, { connectorService, graphRepository });

  app.listen(port, '0.0.0.0', () => {
    console.log(`[NEXUS] Operational server listening on 0.0.0.0:${port}`);
    console.log(`[NEXUS] Environment: ${process.env.NODE_ENV || 'development'}`);
    console.log(`[NEXUS] Repository: ${process.env.NEXUS_REPOSITORY || (process.env.DATABASE_URL ? 'postgres' : 'review/configuration')}`);
    if (connectorService && hasDatabaseConfiguration()) {
      void repository.activeEvent('live').then(event => {
        if (!event) {
          console.info('[NEXUS] No active live event; startup ingestion skipped');
          return;
        }
        return ingestConfiguredFeeds(connectorService, authoritativeConnectors, event.eventId);
      }).catch(error => {
        console.error('[NEXUS] Startup ingestion failed', error);
      });
    }
  });
}
