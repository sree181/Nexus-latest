import { AldotTrafficCountsConnector } from './aldotTrafficCounts.js';
import { CityRoadClosuresConnector } from './cityRoadClosures.js';
import { EtaSpotTransitConnector } from './etaSpotTransit.js';

const connectors = [
  new CityRoadClosuresConnector(),
  new AldotTrafficCountsConnector(),
  new EtaSpotTransitConnector(),
];

for (const connector of connectors) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);
  try {
    const batch = await connector.fetch({
      eventId: '00000000-0000-4000-8000-000000000000',
      requestId: `smoke:${connector.definition.code}`,
      startedAt: new Date().toISOString(),
      signal: controller.signal,
      checkpoint: {},
    });
    console.log(JSON.stringify({
      connector: connector.definition.code,
      authority: connector.definition.authority,
      observationCount: batch.observations.length,
      upstreamObservedAt: batch.upstreamObservedAt,
      sample: batch.observations.slice(0, 2).map(item => ({
        sourceEventId: item.sourceEventId,
        observedAt: item.observedAt,
        summary: item.summary,
        qualityFlags: item.qualityFlags,
      })),
    }, null, 2));
  } finally {
    clearTimeout(timeout);
  }
}
