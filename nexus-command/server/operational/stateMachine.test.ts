import { describe, expect, it } from 'vitest';
import type { Recommendation } from './domain.js';
import {
  applyRecommendationDecision,
  assertCommitmentTransition,
  assertRecommendationSnapshot,
  assertVerifiedEvidence,
} from './stateMachine.js';

function recommendation(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    recommendationId: '44444444-4444-4444-8444-444444444444',
    incidentId: '33333333-3333-4333-8333-333333333333',
    mode: 'live',
    version: 3,
    state: 'awaiting_approval',
    priority: 'high',
    whatChanged: 'Verified congestion threshold crossed',
    whyItMatters: 'Emergency access could be affected',
    recommendedAction: 'Activate approved remote-lot plan',
    expectedEffect: 'Reduce queue growth',
    limitations: 'One source delayed',
    constraints: ['Preserve emergency corridor'],
    evidenceSnapshotHash: 'evidence-snapshot-v3',
    evidence: [],
    approvalRequirements: [],
    agentFindings: [],
    generatedBy: { model: 'policy', version: '1' },
    expiresAt: new Date(Date.now() + 60_000).toISOString(),
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

describe('recommendation state machine', () => {
  it('allows a current live recommendation to be approved', () => {
    expect(applyRecommendationDecision(recommendation(), 'approve')).toBe('approved');
  });

  it('rejects operational decisions against non-live modes', () => {
    expect(() => applyRecommendationDecision(recommendation({ mode: 'training' }), 'approve'))
      .toThrowError(/only be submitted against live recommendations/i);
  });

  it('rejects expired recommendations', () => {
    expect(() => applyRecommendationDecision(recommendation({ expiresAt: new Date(Date.now() - 1_000).toISOString() }), 'approve'))
      .toThrowError(/expired/i);
  });

  it('rejects an invalid transition', () => {
    expect(() => applyRecommendationDecision(recommendation({ state: 'approved' }), 'approve'))
      .toThrowError(/cannot approve/i);
  });

  it('binds the decision to the exact version, state, and evidence snapshot', () => {
    expect(() => assertRecommendationSnapshot(recommendation(), 2, 'awaiting_approval', 'evidence-snapshot-v3'))
      .toThrowError(/changed after it was opened/i);
    expect(() => assertRecommendationSnapshot(recommendation(), 3, 'delegated', 'evidence-snapshot-v3'))
      .toThrowError(/state changed/i);
    expect(() => assertRecommendationSnapshot(recommendation(), 3, 'awaiting_approval', 'different-evidence'))
      .toThrowError(/material evidence changed/i);
  });
});

describe('commitment state machine', () => {
  it('allows requested → acknowledged → approved → executing', () => {
    expect(() => assertCommitmentTransition('requested', 'acknowledged')).not.toThrow();
    expect(() => assertCommitmentTransition('acknowledged', 'approved')).not.toThrow();
    expect(() => assertCommitmentTransition('approved', 'executing')).not.toThrow();
  });

  it('rejects skipping directly from requested to verified', () => {
    expect(() => assertCommitmentTransition('requested', 'verified')).toThrowError(/cannot move/i);
  });

  it('requires evidence before verification', () => {
    expect(() => assertVerifiedEvidence('verified', [])).toThrowError(/without authoritative evidence/i);
    expect(() => assertVerifiedEvidence('verified', ['55555555-5555-4555-8555-555555555555'])).not.toThrow();
  });
});
