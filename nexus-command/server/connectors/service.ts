import { randomUUID } from 'node:crypto';
import type { ConnectorTrigger } from '../operational/domain.js';
import { ConnectorError, type AuthoritativeConnector, type ConnectorStatusView } from './types.js';
import type { ConnectorStore } from './store.js';

export class ConnectorService {
  constructor(private readonly connectors: AuthoritativeConnector[], private readonly store: ConnectorStore) {}

  async initialize(): Promise<void> {
    await this.store.register(this.connectors.map(connector => connector.definition));
  }

  async statuses(): Promise<ConnectorStatusView[]> {
    return this.store.statuses(this.connectors.map(connector => connector.definition));
  }

  async run(code: string, eventId: string, trigger: ConnectorTrigger = 'manual', requestId: string = randomUUID()): Promise<ConnectorStatusView[]> {
    const connector = this.connectors.find(item => item.definition.code === code);
    if (!connector) throw new ConnectorError('configuration_required', `Unknown connector: ${code}`);
    const startedAt = new Date();
    const run = await this.store.beginRun(code, eventId, requestId, trigger);
    if (!run.claimed) return this.statuses();
    if (!connector.isConfigured()) {
      const category = connector.definition.partnerApprovalRequired ? 'permission_required' : 'configuration_required';
      await this.store.completeRun(run, {
        status: 'disabled', upstreamObservedAt: null, fetchedCount: 0, acceptedCount: 0,
        duplicateCount: 0, rejectedCount: 0, durationMs: Date.now() - startedAt.getTime(),
        errorCategory: category, errorDetail: `${connector.definition.name} is not configured`,
      });
      return this.statuses();
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Number(process.env.CONNECTOR_RUN_TIMEOUT_MS ?? 65_000));
    try {
      const batch = await connector.fetch({
        eventId, requestId, startedAt: startedAt.toISOString(), signal: controller.signal, checkpoint: run.checkpoint,
      });
      const counts = await this.store.ingest(run, eventId, batch);
      await this.store.completeRun(run, {
        status: counts.rejectedCount > 0 ? 'partial' : 'succeeded',
        upstreamObservedAt: batch.upstreamObservedAt,
        fetchedCount: batch.observations.length,
        ...counts,
        durationMs: Date.now() - startedAt.getTime(),
        checkpointAfter: batch.checkpoint,
        metadata: batch.metadata,
      });
    } catch (error) {
      const connectorError = error instanceof ConnectorError ? error : new ConnectorError('network_error', error instanceof Error ? error.message : String(error));
      await this.store.completeRun(run, {
        status: 'failed', upstreamObservedAt: null, fetchedCount: 0, acceptedCount: 0,
        duplicateCount: 0, rejectedCount: 0, durationMs: Date.now() - startedAt.getTime(),
        errorCategory: connectorError.category, errorDetail: connectorError.message,
      });
      throw connectorError;
    } finally {
      clearTimeout(timer);
    }
    return this.statuses();
  }
}
