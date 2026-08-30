import { OperationalError } from '../operational/errors.js';
import type { OperationalRepository } from '../operational/repository.js';
import type {
  GraphEntityKind,
  GraphIngestionBatch,
  GraphIngestionResult,
  GraphQueryContext,
  GraphSnapshot,
  GraphStateChange,
  GraphView,
} from './domain.js';
import { projectAgencyCoordination, projectDecisionLineage } from './projectLineage.js';
import type { GraphRepository } from './repository.js';

/**
 * Serves Decision Lineage and Agency Coordination from operational records so the
 * Lineage tab stays truthful when no graph batch has been ingested. Mobility and
 * ingest still use the temporal store when one is configured.
 */
export class OperationalGraphRepository implements GraphRepository {
  constructor(
    private readonly operational: OperationalRepository,
    private readonly persistent?: GraphRepository,
  ) {}

  private requirePersistent(): GraphRepository {
    if (!this.persistent) {
      throw new OperationalError(503, 'GRAPH_STORAGE_NOT_CONFIGURED', 'Persistent graph storage is not configured');
    }
    return this.persistent;
  }

  ingestBatch(
    eventId: string,
    sourceId: string,
    batch: GraphIngestionBatch,
    context: GraphQueryContext,
    idempotencyKey: string,
    requestId: string,
  ): Promise<GraphIngestionResult> {
    return this.requirePersistent().ingestBatch(eventId, sourceId, batch, context, idempotencyKey, requestId);
  }

  async snapshot(eventId: string, view: GraphView, asOf: string, context: GraphQueryContext): Promise<GraphSnapshot> {
    if (view === 'decision_lineage' || view === 'agency_coordination') {
      const lineage = await this.operational.eventLineage(eventId, context.principal);
      return view === 'decision_lineage'
        ? projectDecisionLineage(lineage, asOf)
        : projectAgencyCoordination(lineage, asOf);
    }
    if (this.persistent) return this.persistent.snapshot(eventId, view, asOf, context);
    return {
      eventId,
      mode: context.mode,
      view,
      asOf,
      nodes: [],
      edges: [],
      generatedAt: asOf,
    };
  }

  neighborhood(eventId: string, nodeId: string, depth: number, context: GraphQueryContext): Promise<GraphSnapshot> {
    if (this.persistent) return this.persistent.neighborhood(eventId, nodeId, depth, context);
    return this.snapshot(eventId, 'decision_lineage', new Date().toISOString(), context).then(snapshot => {
      const keep = new Set<string>([nodeId]);
      for (let step = 0; step < depth; step += 1) {
        for (const edge of snapshot.edges) {
          if (keep.has(edge.fromNodeId)) keep.add(edge.toNodeId);
          if (keep.has(edge.toNodeId)) keep.add(edge.fromNodeId);
        }
      }
      return {
        ...snapshot,
        nodes: snapshot.nodes.filter(node => keep.has(node.nodeId)),
        edges: snapshot.edges.filter(edge => keep.has(edge.fromNodeId) && keep.has(edge.toNodeId)),
      };
    });
  }

  stateHistory(kind: GraphEntityKind, entityId: string, limit: number, context: GraphQueryContext): Promise<GraphStateChange[]> {
    if (this.persistent) return this.persistent.stateHistory(kind, entityId, limit, context);
    return Promise.resolve([]);
  }

  async decisionLineage(recommendationId: string, context: GraphQueryContext): Promise<Record<string, unknown>> {
    if (this.persistent) return this.persistent.decisionLineage(recommendationId, context);
    const recommendation = await this.operational.recommendation(recommendationId, context.principal);
    const lineage = await this.operational.eventLineage(
      (await this.operational.activeEvent(context.mode))?.eventId ?? recommendation.incidentId,
      context.principal,
    );
    return {
      recommendation,
      incident: lineage.incidents.find(item => item.incidentId === recommendation.incidentId) ?? null,
      evidence: recommendation.evidence,
      decisions: lineage.decisions.filter(item => item.recommendationId === recommendationId),
      commitments: lineage.commitments.filter(item => item.recommendationId === recommendationId),
    };
  }

  async agencyCoordination(eventId: string, context: GraphQueryContext): Promise<Record<string, unknown>> {
    if (this.persistent) return this.persistent.agencyCoordination(eventId, context);
    const lineage = await this.operational.eventLineage(eventId, context.principal);
    return {
      eventId,
      mode: context.mode,
      commitments: lineage.commitments.filter(item => !['verified', 'failed', 'expired', 'cancelled'].includes(item.state)),
      generatedAt: new Date().toISOString(),
    };
  }
}
