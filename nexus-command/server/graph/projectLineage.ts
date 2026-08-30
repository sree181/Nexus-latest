import { createHash } from 'node:crypto';
import type { EventLineage } from '../operational/repository.js';
import type { GraphEdge, GraphNode, GraphSnapshot } from './domain.js';

const AUTHORITY = 'https://nexus.coordinate/operational-lineage';

function stableId(kind: string, key: string): string {
  const hex = createHash('sha256').update(`${kind}:${key}`).digest('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

function node(
  event: OperationalEvent,
  input: {
    nodeId: string;
    nodeType: string;
    externalKey: string;
    label: string;
    state: Record<string, unknown>;
    qualityFlags?: string[];
    validFrom: string;
    updatedAt: string;
    version?: number;
    ownerAgencyId?: string | null;
  },
): GraphNode {
  return {
    nodeId: input.nodeId,
    eventId: event.eventId,
    mode: event.mode,
    nodeType: input.nodeType,
    externalKey: input.externalKey,
    label: input.label,
    ownerAgencyId: input.ownerAgencyId ?? event.commandOwner?.agencyId ?? null,
    sourceId: null,
    authorityUri: AUTHORITY,
    dataClassification: 'operational',
    geometryGeojson: null,
    state: input.state,
    qualityFlags: input.qualityFlags ?? [],
    validFrom: input.validFrom,
    validUntil: null,
    active: !(input.qualityFlags ?? []).includes('historical'),
    version: input.version ?? 1,
    updatedAt: input.updatedAt,
  };
}

function edge(
  event: OperationalEvent,
  input: {
    edgeType: string;
    fromNodeId: string;
    toNodeId: string;
    validFrom: string;
    state?: Record<string, unknown>;
  },
): GraphEdge {
  return {
    edgeId: stableId('edge', `${input.edgeType}:${input.fromNodeId}:${input.toNodeId}`),
    eventId: event.eventId,
    mode: event.mode,
    edgeType: input.edgeType,
    externalKey: `${input.edgeType}:${input.fromNodeId}:${input.toNodeId}`,
    fromNodeId: input.fromNodeId,
    toNodeId: input.toNodeId,
    directed: true,
    ownerAgencyId: event.commandOwner?.agencyId ?? null,
    sourceId: null,
    authorityUri: AUTHORITY,
    dataClassification: 'operational',
    geometryGeojson: null,
    state: input.state ?? {},
    qualityFlags: [],
    validFrom: input.validFrom,
    validUntil: null,
    active: true,
    version: 1,
    updatedAt: input.validFrom,
  };
}

/**
 * Projects the operational record — evidence through verification — into a graph snapshot.
 * Historical decisions stay on the graph and are flagged; nothing is invented.
 */
export function projectDecisionLineage(lineage: EventLineage, asOf = new Date().toISOString()): GraphSnapshot {
  const { event } = lineage;
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const seen = new Set<string>();

  const addNode = (item: GraphNode) => {
    if (seen.has(item.nodeId)) return;
    seen.add(item.nodeId);
    nodes.push(item);
  };

  for (const incident of lineage.incidents) {
    addNode(node(event, {
      nodeId: incident.incidentId,
      nodeType: 'incident',
      externalKey: incident.incidentId,
      label: incident.title,
      state: {
        severity: incident.severity,
        status: incident.status,
        whatChanged: incident.whatChanged,
        whyItMatters: incident.whyItMatters,
      },
      validFrom: incident.detectedAt,
      updatedAt: incident.updatedAt,
      version: incident.version,
    }));
  }

  const latestDecisionByRecommendation = new Map<string, Decision>();
  for (const decision of [...lineage.decisions].sort((left, right) => Date.parse(left.decidedAt) - Date.parse(right.decidedAt))) {
    latestDecisionByRecommendation.set(decision.recommendationId, decision);
  }

  for (const recommendation of lineage.recommendations) {
    const incident = lineage.incidents.find(item => item.incidentId === recommendation.incidentId);
    addNode(node(event, {
      nodeId: recommendation.recommendationId,
      nodeType: 'recommendation',
      externalKey: recommendation.recommendationId,
      label: recommendation.recommendedAction,
      state: {
        state: recommendation.state,
        priority: recommendation.priority,
        whyItMatters: recommendation.whyItMatters,
        expectedEffect: recommendation.expectedEffect,
        limitations: recommendation.limitations,
      },
      qualityFlags: ['approved', 'rejected', 'expired', 'superseded'].includes(recommendation.state) ? ['closed'] : ['current'],
      validFrom: recommendation.createdAt,
      updatedAt: recommendation.updatedAt,
      version: recommendation.version,
    }));
    if (incident) {
      edges.push(edge(event, {
        edgeType: 'triggered',
        fromNodeId: incident.incidentId,
        toNodeId: recommendation.recommendationId,
        validFrom: recommendation.createdAt,
      }));
    }

    for (const item of recommendation.evidence) {
      addNode(node(event, {
        nodeId: item.evidenceId,
        nodeType: 'evidence',
        externalKey: item.evidenceId,
        label: item.summary,
        state: {
          sourceName: item.sourceName,
          observedAt: item.observedAt,
          qualityFlags: item.qualityFlags,
        },
        qualityFlags: item.qualityFlags,
        validFrom: item.observedAt,
        updatedAt: item.receivedAt,
        ownerAgencyId: null,
      }));
      edges.push(edge(event, {
        edgeType: 'supports',
        fromNodeId: item.evidenceId,
        toNodeId: recommendation.recommendationId,
        validFrom: item.observedAt,
      }));
      if (incident) {
        edges.push(edge(event, {
          edgeType: 'supports',
          fromNodeId: item.evidenceId,
          toNodeId: incident.incidentId,
          validFrom: item.observedAt,
        }));
      }
    }

    for (const finding of recommendation.agentFindings) {
      const findingId = stableId('finding', `${recommendation.incidentId}:${finding.agentCode}:${finding.createdAt}`);
      addNode(node(event, {
        nodeId: findingId,
        nodeType: 'finding',
        externalKey: `${finding.agentCode}:${recommendation.incidentId}`,
        label: `${finding.agentName} · ${finding.status}`,
        state: {
          agentCode: finding.agentCode,
          status: finding.status,
          observation: finding.observation,
          interpretation: finding.interpretation,
          candidateAction: finding.candidateAction,
          limitations: finding.limitations,
        },
        qualityFlags: finding.status === 'abstained' ? ['abstained'] : ['contributed'],
        validFrom: finding.createdAt,
        updatedAt: finding.createdAt,
        ownerAgencyId: null,
      }));
      if (incident) {
        edges.push(edge(event, {
          edgeType: 'informed',
          fromNodeId: findingId,
          toNodeId: incident.incidentId,
          validFrom: finding.createdAt,
        }));
      }
      edges.push(edge(event, {
        edgeType: 'informed',
        fromNodeId: findingId,
        toNodeId: recommendation.recommendationId,
        validFrom: finding.createdAt,
      }));
      for (const evidenceId of finding.citedEvidenceIds) {
        if (!seen.has(evidenceId)) continue;
        edges.push(edge(event, {
          edgeType: 'supports',
          fromNodeId: evidenceId,
          toNodeId: findingId,
          validFrom: finding.createdAt,
        }));
      }
    }
  }

  for (const decision of lineage.decisions) {
    const latest = latestDecisionByRecommendation.get(decision.recommendationId);
    const historical = latest !== undefined && latest.decisionId !== decision.decisionId;
    addNode(node(event, {
      nodeId: decision.decisionId,
      nodeType: 'decision',
      externalKey: decision.decisionId,
      label: `${decision.action.replace(/_/g, ' ')} · ${decision.actor.displayName}`,
      state: {
        action: decision.action,
        actor: decision.actor.displayName,
        agency: decision.actor.agencyName,
        reasonCode: decision.reasonCode,
        comment: decision.comment,
        decidedAt: decision.decidedAt,
      },
      qualityFlags: historical ? ['historical'] : ['current'],
      validFrom: decision.decidedAt,
      updatedAt: decision.decidedAt,
      ownerAgencyId: decision.actor.agencyId,
    }));
    edges.push(edge(event, {
      edgeType: decision.action === 'approve' ? 'approved' : decision.action,
      fromNodeId: decision.recommendationId,
      toNodeId: decision.decisionId,
      validFrom: decision.decidedAt,
      state: { action: decision.action },
    }));
  }

  for (const commitment of lineage.commitments) {
    addNode(node(event, {
      nodeId: commitment.commitmentId,
      nodeType: 'commitment',
      externalKey: commitment.commitmentId,
      label: commitment.requestedOutcome,
      state: {
        state: commitment.state,
        ownerAgencyName: commitment.ownerAgencyName,
        dueAt: commitment.dueAt,
        verificationRule: commitment.verificationRule,
        blocker: commitment.blocker,
      },
      qualityFlags: ['verified', 'failed', 'expired', 'cancelled'].includes(commitment.state) ? ['closed'] : ['current'],
      validFrom: commitment.updatedAt,
      updatedAt: commitment.updatedAt,
      version: commitment.version,
      ownerAgencyId: commitment.ownerAgencyId,
    }));
    if (seen.has(commitment.decisionId)) {
      edges.push(edge(event, {
        edgeType: 'assigned',
        fromNodeId: commitment.decisionId,
        toNodeId: commitment.commitmentId,
        validFrom: commitment.updatedAt,
      }));
    } else if (seen.has(commitment.recommendationId)) {
      edges.push(edge(event, {
        edgeType: 'assigned',
        fromNodeId: commitment.recommendationId,
        toNodeId: commitment.commitmentId,
        validFrom: commitment.updatedAt,
      }));
    }
    if (commitment.state === 'verified') {
      const verificationId = stableId('verification', commitment.commitmentId);
      addNode(node(event, {
        nodeId: verificationId,
        nodeType: 'verification',
        externalKey: verificationId,
        label: commitment.verificationRule,
        state: { commitmentId: commitment.commitmentId, state: commitment.state },
        qualityFlags: ['current'],
        validFrom: commitment.updatedAt,
        updatedAt: commitment.updatedAt,
        ownerAgencyId: commitment.ownerAgencyId,
      }));
      edges.push(edge(event, {
        edgeType: 'verified',
        fromNodeId: commitment.commitmentId,
        toNodeId: verificationId,
        validFrom: commitment.updatedAt,
      }));
    }
  }

  return {
    eventId: event.eventId,
    mode: event.mode,
    view: 'decision_lineage',
    asOf,
    nodes,
    edges,
    generatedAt: asOf,
  };
}

export function projectAgencyCoordination(lineage: EventLineage, asOf = new Date().toISOString()): GraphSnapshot {
  const snapshot = projectDecisionLineage(lineage, asOf);
  const keep = new Set(['commitment', 'decision', 'recommendation', 'incident', 'verification']);
  const nodes = snapshot.nodes.filter(item => keep.has(item.nodeType));
  const ids = new Set(nodes.map(item => item.nodeId));
  return {
    ...snapshot,
    view: 'agency_coordination',
    nodes,
    edges: snapshot.edges.filter(item => ids.has(item.fromNodeId) && ids.has(item.toNodeId)),
  };
}
