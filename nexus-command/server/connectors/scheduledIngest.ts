import { projectLiveAdvisory } from '../operational/evidenceAdvisory.js';
import type { ConnectorService } from './service.js';
import type { AuthoritativeConnector } from './types.js';

export async function ingestConfiguredFeeds(
  service: ConnectorService,
  connectors: AuthoritativeConnector[],
  eventId: string,
): Promise<void> {
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
    await projectLiveAdvisory(eventId);
  } catch (error) {
    console.error('[connectors] Advisory projection failed', error);
  }
}
