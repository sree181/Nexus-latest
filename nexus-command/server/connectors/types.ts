import type {
  DataClassification,
  NormalizedObservation,
  SourceConnectionStatus,
} from '../operational/domain.js';

export interface ConnectorDefinition {
  code: string;
  sourceCode: string;
  name: string;
  ownerAgencyCode: string;
  ownerAgencyName: string;
  sourceType: 'api' | 'poll' | 'webhook' | 'file';
  authority: string;
  authorityUri: string;
  schemaVersion: string;
  expectedCadenceSeconds: number | null;
  staleAfterSeconds: number;
  dataClassification: DataClassification;
  permittedUse: string;
  partnerApprovalRequired: boolean;
  defaultConnectionStatus: SourceConnectionStatus;
  requiredEnvironment: string[];
}

export interface ConnectorContext {
  eventId: string;
  requestId: string;
  startedAt: string;
  signal: AbortSignal;
  checkpoint: Record<string, unknown>;
}

export interface ConnectorBatch {
  observations: NormalizedObservation[];
  upstreamObservedAt: string | null;
  checkpoint: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface AuthoritativeConnector {
  readonly definition: ConnectorDefinition;
  isConfigured(): boolean;
  fetch(context: ConnectorContext): Promise<ConnectorBatch>;
}

export interface ConnectorStatusView {
  definition: ConnectorDefinition;
  sourceId: string | null;
  configured: boolean;
  connectionStatus: SourceConnectionStatus;
  healthStatus: 'healthy' | 'delayed' | 'unavailable' | 'unverified' | 'disabled';
  lastAttemptAt: string | null;
  lastSuccessAt: string | null;
  lastEventObservedAt: string | null;
  consecutiveFailures: number;
  latestRun: {
    runId: string;
    status: string;
    startedAt: string;
    completedAt: string | null;
    fetchedCount: number;
    acceptedCount: number;
    duplicateCount: number;
    rejectedCount: number;
    errorCategory: string | null;
  } | null;
}

export type ConnectorErrorCategory =
  | 'configuration_required'
  | 'permission_required'
  | 'rate_limited'
  | 'upstream_timeout'
  | 'upstream_unavailable'
  | 'invalid_payload'
  | 'network_error';

export class ConnectorError extends Error {
  constructor(
    readonly category: ConnectorErrorCategory,
    message: string,
    readonly httpStatus?: number,
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = 'ConnectorError';
  }
}
