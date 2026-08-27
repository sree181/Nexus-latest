import { describe, expect, it, vi } from 'vitest';
import { ConnectorService } from './service.js';
import type { ConnectorRunCompletion, ConnectorRunStart, ConnectorStore } from './store.js';
import type { AuthoritativeConnector, ConnectorDefinition, ConnectorStatusView } from './types.js';

function definition(overrides: Partial<ConnectorDefinition> = {}): ConnectorDefinition {
  return {
    code: 'test-live-v1', sourceCode: 'test-live', name: 'Test authoritative source',
    ownerAgencyCode: 'test-agency', ownerAgencyName: 'Test Agency', sourceType: 'api',
    authority: 'Test Agency', authorityUri: 'https://authority.example/source', schemaVersion: '1.0.0',
    expectedCadenceSeconds: 30, staleAfterSeconds: 120, dataClassification: 'live',
    permittedUse: 'Connector orchestration test', partnerApprovalRequired: false,
    defaultConnectionStatus: 'connected', requiredEnvironment: [], ...overrides,
  };
}

class TestStore implements ConnectorStore {
  definitions: ConnectorDefinition[] = [];
  completions: ConnectorRunCompletion[] = [];
  claimed = true;
  ingestCalls = 0;

  async register(definitions: ConnectorDefinition[]) { this.definitions = definitions; }
  async statuses(definitions: ConnectorDefinition[]): Promise<ConnectorStatusView[]> {
    return definitions.map(item => ({
      definition: item, sourceId: 'source-1', configured: true,
      connectionStatus: this.completions.at(-1)?.errorCategory === 'permission_required' ? 'permission_required' : item.defaultConnectionStatus,
      healthStatus: this.completions.at(-1)?.status === 'disabled' ? 'disabled' : 'healthy',
      lastAttemptAt: null, lastSuccessAt: null, lastEventObservedAt: null, consecutiveFailures: 0, latestRun: null,
    }));
  }
  async beginRun(): Promise<ConnectorRunStart> {
    return { runId: 'run-1', sourceId: 'source-1', checkpoint: {}, claimed: this.claimed };
  }
  async ingest() { this.ingestCalls += 1; return { acceptedCount: 0, duplicateCount: 0, rejectedCount: 0 }; }
  async completeRun(_run: ConnectorRunStart, completion: ConnectorRunCompletion) { this.completions.push(completion); }
}

describe('ConnectorService', () => {
  it('registers authoritative connector definitions and completes a successful run', async () => {
    const store = new TestStore();
    const fetch = vi.fn(async () => ({ observations: [], upstreamObservedAt: new Date().toISOString(), checkpoint: {}, metadata: {} }));
    const connector: AuthoritativeConnector = { definition: definition(), isConfigured: () => true, fetch };
    const service = new ConnectorService([connector], store);
    await service.initialize();
    await service.run(connector.definition.code, 'event-1', 'scheduled', 'scheduled:test:1');
    expect(store.definitions).toEqual([connector.definition]);
    expect(fetch).toHaveBeenCalledOnce();
    expect(store.ingestCalls).toBe(1);
    expect(store.completions[0]?.status).toBe('succeeded');
  });

  it('does not refetch when another worker already claimed the cadence request ID', async () => {
    const store = new TestStore();
    store.claimed = false;
    const fetch = vi.fn();
    const connector: AuthoritativeConnector = { definition: definition(), isConfigured: () => true, fetch };
    await new ConnectorService([connector], store).run(connector.definition.code, 'event-1', 'scheduled', 'scheduled:test:1');
    expect(fetch).not.toHaveBeenCalled();
    expect(store.ingestCalls).toBe(0);
  });

  it('records partner-gated connectors as permission required without calling upstream', async () => {
    const store = new TestStore();
    const fetch = vi.fn();
    const connector: AuthoritativeConnector = {
      definition: definition({ partnerApprovalRequired: true, defaultConnectionStatus: 'permission_required', requiredEnvironment: ['PARTNER_TOKEN'] }),
      isConfigured: () => false,
      fetch,
    };
    const statuses = await new ConnectorService([connector], store).run(connector.definition.code, 'event-1', 'manual', 'manual:test:1');
    expect(fetch).not.toHaveBeenCalled();
    expect(store.completions[0]).toMatchObject({ status: 'disabled', errorCategory: 'permission_required' });
    expect(statuses[0]?.connectionStatus).toBe('permission_required');
  });
});
