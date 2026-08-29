/**
 * Nexus Coordinate — Simulation-Free Operational Server
 *
 * The live application exposes evidence, incidents, recommendations, human
 * decisions, agency commitments, audit records, and realtime updates. Legacy
 * simulation and MARL playback routes are intentionally not mounted.
 */
import { createApp } from './app.js';
import { createOperationalRepository } from './operational/repositoryFactory.js';
import { hasDatabaseConfiguration } from './operational/database.js';
import { authoritativeConnectors } from './connectors/registry.js';
import { PostgresConnectorStore } from './connectors/postgresStore.js';
import { ingestConfiguredFeeds } from './connectors/scheduledIngest.js';
import { ConnectorService } from './connectors/service.js';
import { PostgresGraphRepository } from './graph/postgresRepository.js';

const port = Number(process.env.PORT || process.env.AGENT_PORT || 4002);
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
