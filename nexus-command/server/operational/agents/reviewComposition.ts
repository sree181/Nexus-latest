/**
 * Local review still uses labeled practice data. The desks, though, are the same
 * functions that run after detection: ATLAS and AQUA may use the tool loop,
 * everyone else uses their assessor, NEXUS still does not invent a different action.
 */
import type { DetectionEvidence, DetectionMatch } from '../detection/rules.js';
import type { AgentFinding, Recommendation } from '../domain.js';
import { composeDeskFindings, type DeskFinding } from './orchestrator.js';

export const REVIEW_TOMTOM_ID = '55555555-5555-4555-8555-555555555551';
export const REVIEW_TRANSIT_ID = '55555555-5555-4555-8555-555555555553';
export const REVIEW_SIGN_ID = '55555555-5555-4555-8555-555555555554';

const REVIEW_STAFFED = ['atlas', 'aqua', 'sentinel', 'phoenix', 'echo', 'nexus'];

function evidence(
  evidenceId: string,
  connectorCode: string,
  sourceName: string,
  summary: string,
  attributes: Record<string, unknown>,
  observedAt: string,
): DetectionEvidence {
  return {
    evidenceId,
    connectorCode,
    sourceEventId: `review:${evidenceId}`,
    sourceName,
    summary,
    observedAt,
    contentHash: `review:${evidenceId}`,
    geometryGeojson: null,
    attributes,
  };
}

export function reviewDeskSnapshot(recommendation: Recommendation): {
  match: DetectionMatch;
  snapshot: DetectionEvidence[];
  liveConnectors: string[];
} {
  const observedAt = recommendation.evidence[0]?.observedAt ?? new Date().toISOString();
  const snapshot = [
    evidence(
      REVIEW_TOMTOM_ID,
      'tomtom-traffic-flow-v1',
      'TomTom Traffic Flow',
      'Approach speed has remained below the event threshold for eight minutes.',
      {
        layer: 'travel_time',
        congestionLevel: 'Heavy',
        currentSpeedMph: 18,
        freeFlowSpeedMph: 42,
        corridor: 'Remote-lot approach',
        trend: 'deteriorating',
      },
      observedAt,
    ),
    evidence(
      REVIEW_TRANSIT_ID,
      'auburn-eta-spot-v1',
      'Tiger Transit Operations',
      'Two shuttles are available for event staging.',
      { availableUnits: 2, currentDelayMinutes: 4 },
      observedAt,
    ),
    evidence(
      REVIEW_SIGN_ID,
      'aldot-algo-traffic-v1',
      'ALGO Traffic',
      'I-85 message sign southbound Auburn is displaying traveler text.',
      { layer: 'message_sign', pages: ['EVENT TRAFFIC / FOLLOW DETOUR'] },
      observedAt,
    ),
  ];

  const primary = snapshot[0];
  const match: DetectionMatch = {
    rule: {
      packCode: 'sec_gameday',
      ruleCode: 'review-arrival-congestion',
      connectorCode: 'tomtom-traffic-flow-v1',
      agentCode: 'atlas',
      name: 'Arrival congestion on the remote-lot approach',
      whyItMatters: recommendation.whyItMatters,
      severity: recommendation.priority,
      affectedServices: ['traffic', 'parking', 'transit'],
      constraints: recommendation.constraints,
      playbook: {
        recommendedAction: recommendation.recommendedAction,
        expectedEffect: recommendation.expectedEffect,
        limitations: recommendation.limitations,
        approvals: [],
        commitments: [],
      },
    },
    externalKey: 'review-arrival-congestion',
    title: 'Arrival congestion — remote-lot approach',
    whatChanged: recommendation.whatChanged,
    severity: recommendation.priority,
    primary,
    evidence: snapshot,
    evidenceHash: recommendation.evidenceSnapshotHash,
  };

  return {
    match,
    snapshot,
    liveConnectors: ['tomtom-traffic-flow-v1', 'auburn-eta-spot-v1', 'aldot-algo-traffic-v1'],
  };
}

function toAgentFinding(finding: DeskFinding, createdAt: string): AgentFinding {
  return {
    agentCode: finding.agentCode,
    agentName: finding.agentName,
    status: finding.status,
    observation: finding.observation,
    interpretation: finding.interpretation,
    candidateAction: finding.candidateAction,
    confidence: finding.confidence,
    limitations: finding.limitations,
    citedEvidenceIds: finding.citedEvidenceIds,
    conflicts: finding.conflicts,
    createdAt,
    modelName: finding.modelName,
    modelVersion: finding.modelVersion,
  };
}

export async function composeReviewDeskFindings(recommendation: Recommendation): Promise<AgentFinding[]> {
  const inputs = reviewDeskSnapshot(recommendation);
  const composition = await composeDeskFindings({
    staffedAgentCodes: REVIEW_STAFFED,
    match: inputs.match,
    snapshot: inputs.snapshot,
    liveConnectors: inputs.liveConnectors,
  });
  const createdAt = recommendation.createdAt;
  return composition.findings.map(finding => toAgentFinding(finding, createdAt));
}
