import type {
  CommitmentState,
  DecisionAction,
  OperationalMode,
  Recommendation,
  RecommendationState,
} from './domain.js';
import { conflict, validation } from './errors.js';

const recommendationTransitions: Record<DecisionAction, RecommendationState[]> = {
  acknowledge: ['awaiting_acknowledgement'],
  approve: ['awaiting_approval', 'delegated'],
  reject: ['awaiting_acknowledgement', 'awaiting_approval', 'delegated', 'escalated'],
  request_revision: ['awaiting_acknowledgement', 'awaiting_approval', 'delegated', 'escalated'],
  delegate: ['awaiting_approval'],
  escalate: ['awaiting_acknowledgement', 'awaiting_approval', 'delegated'],
  withdraw: ['approved'],
};

const recommendationTargets: Record<DecisionAction, RecommendationState> = {
  acknowledge: 'awaiting_approval',
  approve: 'approved',
  reject: 'rejected',
  request_revision: 'revision_requested',
  delegate: 'delegated',
  escalate: 'escalated',
  withdraw: 'superseded',
};

const commitmentTransitions: Record<CommitmentState, CommitmentState[]> = {
  requested: ['acknowledged', 'cancelled', 'expired'],
  acknowledged: ['approved', 'blocked', 'cancelled', 'expired'],
  approved: ['executing', 'blocked', 'cancelled', 'expired'],
  executing: ['verified', 'blocked', 'failed'],
  blocked: ['acknowledged', 'approved', 'executing', 'failed', 'cancelled'],
  verified: [],
  failed: [],
  expired: [],
  cancelled: [],
};

export function assertModeAllowed(mode: OperationalMode, principalModes: OperationalMode[]): void {
  if (!principalModes.includes(mode)) {
    throw conflict('MODE_ACCESS_DENIED', `Principal is not authorized for ${mode} mode`, { mode });
  }
}

export function applyRecommendationDecision(recommendation: Recommendation, action: DecisionAction, now = new Date()): RecommendationState {
  if (recommendation.mode !== 'live') {
    throw conflict('NON_LIVE_DECISION_REJECTED', 'Operational decisions can only be submitted against live recommendations');
  }
  if (new Date(recommendation.expiresAt).getTime() <= now.getTime()) {
    throw conflict('RECOMMENDATION_EXPIRED', 'The recommendation approval window has expired', { expiresAt: recommendation.expiresAt });
  }
  if (!recommendationTransitions[action].includes(recommendation.state)) {
    throw conflict('INVALID_RECOMMENDATION_TRANSITION', `Cannot ${action} a recommendation in ${recommendation.state} state`, {
      currentState: recommendation.state,
      action,
    });
  }
  return recommendationTargets[action];
}

export function assertRecommendationSnapshot(
  recommendation: Recommendation,
  expectedVersion: number,
  expectedState: RecommendationState,
  evidenceSnapshotHash: string,
): void {
  if (recommendation.version !== expectedVersion) {
    throw conflict('RECOMMENDATION_VERSION_CONFLICT', 'The recommendation changed after it was opened', {
      expectedVersion,
      actualVersion: recommendation.version,
    });
  }
  if (recommendation.state !== expectedState) {
    throw conflict('RECOMMENDATION_STATE_CONFLICT', 'The recommendation state changed after it was opened', {
      expectedState,
      actualState: recommendation.state,
    });
  }
  if (recommendation.evidenceSnapshotHash !== evidenceSnapshotHash) {
    throw conflict('EVIDENCE_SNAPSHOT_CONFLICT', 'Material evidence changed after the recommendation was opened');
  }
}

export function assertCommitmentTransition(current: CommitmentState, target: CommitmentState): void {
  if (!commitmentTransitions[current].includes(target)) {
    throw conflict('INVALID_COMMITMENT_TRANSITION', `Cannot move commitment from ${current} to ${target}`, {
      currentState: current,
      targetState: target,
    });
  }
}

export function assertVerifiedEvidence(target: CommitmentState, evidenceIds: string[]): void {
  if (target === 'verified' && evidenceIds.length === 0) {
    throw validation('A commitment cannot be verified without authoritative evidence or accountable operator confirmation');
  }
}

export function decisionScope(action: DecisionAction): string {
  return `recommendation:${action}`;
}
