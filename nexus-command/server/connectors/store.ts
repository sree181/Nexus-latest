import type { ConnectorBatch, ConnectorDefinition, ConnectorStatusView } from './types.js';
import type { ConnectorTrigger } from '../operational/domain.js';

export interface ConnectorRunStart {
  runId: string;
  sourceId: string;
  checkpoint: Record<string, unknown>;
  claimed: boolean;
}

export interface ConnectorRunCompletion {
  status: 'succeeded' | 'partial' | 'failed' | 'skipped' | 'disabled';
  upstreamObservedAt: string | null;
  fetchedCount: number;
  acceptedCount: number;
  duplicateCount: number;
  rejectedCount: number;
  durationMs: number;
  errorCategory?: string;
  errorDetail?: string;
  checkpointAfter?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface ObservationIngestResult {
  acceptedCount: number;
  duplicateCount: number;
  rejectedCount: number;
}

export interface ConnectorStore {
  register(definitions: ConnectorDefinition[]): Promise<void>;
  statuses(definitions: ConnectorDefinition[]): Promise<ConnectorStatusView[]>;
  beginRun(connectorCode: string, eventId: string | null, requestId: string, trigger: ConnectorTrigger): Promise<ConnectorRunStart>;
  ingest(run: ConnectorRunStart, eventId: string, batch: ConnectorBatch): Promise<ObservationIngestResult>;
  completeRun(run: ConnectorRunStart, completion: ConnectorRunCompletion): Promise<void>;
}
