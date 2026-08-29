import { afterEach, describe, expect, it } from 'vitest';
import { EtaSpotTransitConnector } from './etaSpotTransit.js';

afterEach(() => {
  delete process.env.ETA_SPOT_PRODUCTION_APPROVED;
  delete process.env.NEXUS_ENABLE_PUBLIC_FEEDS;
});

describe('EtaSpotTransitConnector', () => {
  it('stays gated until an explicit public-feed approval flag is set', () => {
    expect(new EtaSpotTransitConnector().isConfigured()).toBe(false);
    process.env.NEXUS_ENABLE_PUBLIC_FEEDS = 'true';
    expect(new EtaSpotTransitConnector().isConfigured()).toBe(true);
  });
});
