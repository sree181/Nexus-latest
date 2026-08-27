import { describe, expect, it } from 'vitest';
import type { PrincipalContext } from './domain.js';
import { ReviewOperationalRepository } from './reviewRepository.js';

const principal: PrincipalContext = {
  principalId: '11111111-1111-4111-8111-111111111111',
  externalSubject: 'test-operator',
  displayName: 'Jordan Smith',
  agencyId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  agencyName: 'Auburn Event Mobility Command',
  roles: ['event_mobility_lead'],
  scopes: ['recommendation:approve', 'commitment:transition'],
  modes: ['live'],
};

describe('review operational repository', () => {
  it('creates agency commitments only after a human approval', async () => {
    const repository = new ReviewOperationalRepository();
    const event = await repository.activeEvent('live');
    expect(event).not.toBeNull();
    const snapshot = await repository.snapshot(event!.eventId, principal);
    expect(snapshot.decisionQueue).toHaveLength(1);
    expect(snapshot.commitments).toHaveLength(0);

    const recommendation = snapshot.decisionQueue[0];
    const result = await repository.decide(
      recommendation.recommendationId,
      {
        action: 'approve',
        recommendationVersion: recommendation.version,
        expectedState: recommendation.state,
        evidenceSnapshotHash: recommendation.evidenceSnapshotHash,
        reasonCode: 'EVIDENCE_REVIEWED',
      },
      principal,
      'approve-key-001',
      'request-001',
    );

    expect(result.recommendation.state).toBe('approved');
    expect(result.createdCommitments).toHaveLength(2);
    expect(result.createdCommitments.every(item => item.state === 'requested')).toBe(true);
  });

  it('returns the same response for an identical idempotent request', async () => {
    const repository = new ReviewOperationalRepository();
    const recommendation = await repository.recommendation('44444444-4444-4444-8444-444444444444');
    const command = {
      action: 'approve' as const,
      recommendationVersion: recommendation.version,
      expectedState: recommendation.state,
      evidenceSnapshotHash: recommendation.evidenceSnapshotHash,
      reasonCode: 'EVIDENCE_REVIEWED',
    };
    const first = await repository.decide(recommendation.recommendationId, command, principal, 'approve-key-002', 'request-002');
    const second = await repository.decide(recommendation.recommendationId, command, principal, 'approve-key-002', 'request-003');
    expect(second.decision.decisionId).toBe(first.decision.decisionId);
  });

  it('rejects an idempotency key reused for a different decision', async () => {
    const repository = new ReviewOperationalRepository();
    const recommendation = await repository.recommendation('44444444-4444-4444-8444-444444444444');
    await repository.decide(
      recommendation.recommendationId,
      {
        action: 'approve', recommendationVersion: 3, expectedState: 'awaiting_approval',
        evidenceSnapshotHash: recommendation.evidenceSnapshotHash, reasonCode: 'EVIDENCE_REVIEWED',
      },
      principal,
      'approve-key-003',
      'request-004',
    );

    await expect(repository.decide(
      recommendation.recommendationId,
      {
        action: 'reject', recommendationVersion: 3, expectedState: 'awaiting_approval',
        evidenceSnapshotHash: recommendation.evidenceSnapshotHash, reasonCode: 'NOT_AUTHORIZED',
      },
      principal,
      'approve-key-003',
      'request-005',
    )).rejects.toMatchObject({ code: 'IDEMPOTENCY_KEY_REUSED' });
  });

  it('enforces sequential commitment transitions', async () => {
    const repository = new ReviewOperationalRepository();
    const recommendation = await repository.recommendation('44444444-4444-4444-8444-444444444444');
    const approved = await repository.decide(
      recommendation.recommendationId,
      {
        action: 'approve', recommendationVersion: 3, expectedState: 'awaiting_approval',
        evidenceSnapshotHash: recommendation.evidenceSnapshotHash, reasonCode: 'EVIDENCE_REVIEWED',
      },
      principal,
      'approve-key-004',
      'request-006',
    );
    const commitment = approved.createdCommitments[0];
    await expect(repository.transitionCommitment(
      commitment.commitmentId,
      { expectedVersion: 1, targetState: 'verified', reasonCode: 'VERIFY', evidenceIds: [] },
      principal,
      'transition-key-001',
      'request-007',
    )).rejects.toMatchObject({ code: 'INVALID_COMMITMENT_TRANSITION' });
  });
});
