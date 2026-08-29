import { withAdvisoryLock } from '../operational/database.js';
import { runDetection } from '../operational/detection/service.js';
import type { ConnectorService } from './service.js';
import type { AuthoritativeConnector } from './types.js';

/**
 * Ingestion and detection for one operating window is a critical section. The API runs a pass at
 * startup and the worker runs one on every tick, so without this the two processes interleave:
 * one writes evidence rows while the other locks the same rows to cite them, and Postgres breaks
 * the cycle by killing a connector run.
 */
export type LockRunner = <T>(key: string, operation: () => Promise<T>) => Promise<T | null>;

export async function ingestConfiguredFeeds(
  service: ConnectorService,
  connectors: AuthoritativeConnector[],
  eventId: string,
  lock: LockRunner = withAdvisoryLock,
): Promise<void> {
  const ran = await lock(`nexus-ingest:${eventId}`, async () => {
    const due = connectors.filter(connector => connector.isConfigured() && connector.definition.expectedCadenceSeconds !== null);
    const results = await Promise.allSettled(due.map(async connector => {
      const cadence = connector.definition.expectedCadenceSeconds ?? 60;
      const bucket = Math.floor(Date.now() / (cadence * 1_000));
      const requestId = `scheduled:${connector.definition.code}:${bucket}`;
      await service.run(connector.definition.code, eventId, 'scheduled', requestId);
      return connector.definition.code;
    }));

    for (const result of results) {
      if (result.status === 'rejected') {
        console.error('[connectors] Ingestion failed', result.reason);
      }
    }

    try {
      await runDetection(eventId);
    } catch (error) {
      console.error('[connectors] Detection pass failed', error);
    }
    return true;
  });

  if (ran === null) {
    console.info('[connectors] Another process holds this operating window; skipped this pass', { eventId });
  }
}
