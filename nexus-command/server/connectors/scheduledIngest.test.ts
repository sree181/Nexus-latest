import { describe, expect, it, vi } from 'vitest';
import { ingestConfiguredFeeds, type LockRunner } from './scheduledIngest.js';
import type { ConnectorService } from './service.js';
import type { AuthoritativeConnector, ConnectorDefinition } from './types.js';

function connector(code: string, expectedCadenceSeconds: number | null, configured = true): AuthoritativeConnector {
  const definition = {
    code, sourceCode: code, name: code,
    ownerAgencyCode: 'test-agency', ownerAgencyName: 'Test Agency', sourceType: 'api',
    authority: 'Test Agency', authorityUri: 'https://authority.example', schemaVersion: '1.0.0',
    expectedCadenceSeconds, staleAfterSeconds: 120, dataClassification: 'live',
    permittedUse: 'Test', partnerApprovalRequired: false,
    defaultConnectionStatus: 'connected', requiredEnvironment: [],
  } as ConnectorDefinition;
  return { definition, isConfigured: () => configured, fetch: vi.fn() };
}

const granting: LockRunner = (_key, operation) => operation();
const held: LockRunner = async () => null;

describe('scheduled ingestion', () => {
  it('runs every configured connector that has a cadence', async () => {
    const run = vi.fn(async () => []);
    const service = { run } as unknown as ConnectorService;
    await ingestConfiguredFeeds(service, [
      connector('with-cadence-v1', 60),
      connector('webhook-only-v1', null),
      connector('unconfigured-v1', 60, false),
    ], 'event-1', granting);

    expect(run.mock.calls.map(call => call[0])).toEqual(['with-cadence-v1']);
  });

  it('skips the pass entirely when another process holds the operating window', async () => {
    const run = vi.fn(async () => []);
    const service = { run } as unknown as ConnectorService;
    await ingestConfiguredFeeds(service, [connector('with-cadence-v1', 60)], 'event-1', held);
    expect(run).not.toHaveBeenCalled();
  });

  it('keeps ingesting the remaining feeds when one connector fails', async () => {
    const run = vi.fn(async (code: string) => {
      if (code === 'broken-v1') throw new Error('upstream refused');
      return [];
    });
    const service = { run } as unknown as ConnectorService;
    await expect(ingestConfiguredFeeds(service, [
      connector('broken-v1', 60),
      connector('healthy-v1', 60),
    ], 'event-1', granting)).resolves.toBeUndefined();
    expect(run.mock.calls.map(call => call[0]).sort()).toEqual(['broken-v1', 'healthy-v1']);
  });
});
