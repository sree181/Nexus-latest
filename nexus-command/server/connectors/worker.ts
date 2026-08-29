import { closeDatabasePool, hasDatabaseConfiguration } from '../operational/database.js';
import { PostgresOperationalRepository } from '../operational/postgresRepository.js';
import { authoritativeConnectors } from './registry.js';
import { PostgresConnectorStore } from './postgresStore.js';
import { ingestConfiguredFeeds } from './scheduledIngest.js';
import { ConnectorService } from './service.js';

if (!hasDatabaseConfiguration()) {
  throw new Error('DATABASE_URL is required for the Nexus connector worker');
}

const configured = authoritativeConnectors.filter(connector => connector.isConfigured());
const service = new ConnectorService(
  authoritativeConnectors,
  new PostgresConnectorStore(new Set(configured.map(connector => connector.definition.code))),
);
const operationalRepository = new PostgresOperationalRepository();
const baseIntervalMs = Math.max(5_000, Number(process.env.CONNECTOR_WORKER_TICK_MS ?? 15_000));
let stopping = false;

await service.initialize();

async function tick(): Promise<void> {
  const event = await operationalRepository.activeEvent('live');
  if (!event) {
    console.info('[connector-worker] No active live operational event; ingestion paused');
    return;
  }

  await ingestConfiguredFeeds(service, configured, event.eventId);
}

async function loop(): Promise<void> {
  while (!stopping) {
    const started = Date.now();
    try {
      await tick();
    } catch (error) {
      console.error('[connector-worker] Tick failed', error);
    }
    const delay = Math.max(1_000, baseIntervalMs - (Date.now() - started));
    await new Promise(resolve => setTimeout(resolve, delay));
  }
}

async function shutdown(signal: string): Promise<void> {
  if (stopping) return;
  stopping = true;
  console.info(`[connector-worker] Received ${signal}; stopping after the current ingestion cycle`);
  await closeDatabasePool();
}

process.on('SIGTERM', () => { void shutdown('SIGTERM'); });
process.on('SIGINT', () => { void shutdown('SIGINT'); });

console.info('[connector-worker] Authoritative ingestion worker started', {
  configuredConnectors: configured.map(connector => connector.definition.code),
  tickMs: baseIntervalMs,
});
await loop();
