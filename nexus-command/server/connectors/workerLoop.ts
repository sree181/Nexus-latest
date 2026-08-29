import { closeDatabasePool } from '../operational/database.js';
import { PostgresOperationalRepository } from '../operational/postgresRepository.js';
import { authoritativeConnectors } from './registry.js';
import { PostgresConnectorStore } from './postgresStore.js';
import { ingestConfiguredFeeds } from './scheduledIngest.js';
import { ConnectorService } from './service.js';

export type TickOutcome = 'ingested' | 'paused' | 'failed';

export interface ConnectorWorkerStatus {
  role: 'connector-worker';
  startedAt: string;
  configuredConnectors: string[];
  tickMs: number;
  ticks: number;
  lastTickAt: string | null;
  lastOutcome: TickOutcome | null;
  lastError: string | null;
  /** False once the loop has gone quiet for several intervals, which means it is wedged. */
  alive: boolean;
}

export interface ConnectorWorkerHandle {
  status(): ConnectorWorkerStatus;
  stop(): Promise<void>;
}

/** A loop that has missed this many intervals is not merely slow. */
const STALL_INTERVALS = 5;

export function startConnectorWorker(): ConnectorWorkerHandle {
  const configured = authoritativeConnectors.filter(connector => connector.isConfigured());
  const service = new ConnectorService(
    authoritativeConnectors,
    new PostgresConnectorStore(new Set(configured.map(connector => connector.definition.code))),
  );
  const repository = new PostgresOperationalRepository();
  const tickMs = Math.max(5_000, Number(process.env.CONNECTOR_WORKER_TICK_MS ?? 15_000));
  const startedAt = new Date().toISOString();

  let stopping = false;
  let ticks = 0;
  let lastTickAt: number | null = null;
  let lastOutcome: TickOutcome | null = null;
  let lastError: string | null = null;

  async function tick(): Promise<void> {
    const event = await repository.activeEvent('live');
    if (!event) {
      console.info('[connector-worker] No active live operational event; ingestion paused');
      lastOutcome = 'paused';
      return;
    }
    await ingestConfiguredFeeds(service, configured, event.eventId);
    lastOutcome = 'ingested';
  }

  async function loop(): Promise<void> {
    await service.initialize();
    console.info('[connector-worker] Authoritative ingestion worker started', {
      configuredConnectors: configured.map(connector => connector.definition.code),
      tickMs,
    });
    while (!stopping) {
      const started = Date.now();
      try {
        await tick();
        lastError = null;
      } catch (error) {
        lastOutcome = 'failed';
        lastError = error instanceof Error ? error.message : String(error);
        console.error('[connector-worker] Tick failed', error);
      }
      ticks += 1;
      lastTickAt = Date.now();
      const delay = Math.max(1_000, tickMs - (Date.now() - started));
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }

  void loop().catch(error => {
    lastOutcome = 'failed';
    lastError = error instanceof Error ? error.message : String(error);
    console.error('[connector-worker] Ingestion loop exited', error);
  });

  return {
    status() {
      const since = lastTickAt === null ? Date.now() - Date.parse(startedAt) : Date.now() - lastTickAt;
      return {
        role: 'connector-worker',
        startedAt,
        configuredConnectors: configured.map(connector => connector.definition.code),
        tickMs,
        ticks,
        lastTickAt: lastTickAt === null ? null : new Date(lastTickAt).toISOString(),
        lastOutcome,
        lastError,
        alive: !stopping && since < tickMs * STALL_INTERVALS,
      };
    },
    async stop() {
      stopping = true;
      await closeDatabasePool();
    },
  };
}
