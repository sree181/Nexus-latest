import type {
  GraphEntityKind,
  GraphIngestionBatch,
  GraphIngestionResult,
  GraphQueryContext,
  GraphSnapshot,
  GraphStateChange,
  GraphView,
} from './domain.js';

export interface GraphRepository {
  ingestBatch(
    eventId: string,
    sourceId: string,
    batch: GraphIngestionBatch,
    context: GraphQueryContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<GraphIngestionResult>;
  snapshot(eventId: string, view: GraphView, asOf: string, context: GraphQueryContext): Promise<GraphSnapshot>;
  neighborhood(eventId: string, nodeId: string, depth: number, context: GraphQueryContext): Promise<GraphSnapshot>;
  stateHistory(kind: GraphEntityKind, entityId: string, limit: number, context: GraphQueryContext): Promise<GraphStateChange[]>;
  decisionLineage(recommendationId: string, context: GraphQueryContext): Promise<Record<string, unknown>>;
  agencyCoordination(eventId: string, context: GraphQueryContext): Promise<Record<string, unknown>>;
}
