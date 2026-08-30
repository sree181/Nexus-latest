import { describe, expect, it } from 'vitest';
import { ReviewOperationalRepository } from '../reviewRepository.js';
import type { PrincipalContext } from '../domain.js';

const principal: PrincipalContext = {
  principalId: '11111111-1111-4111-8111-111111111111',
  externalSubject: 'test-operator',
  displayName: 'Jordan Smith',
  agencyId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  agencyName: 'Auburn Event Mobility Command',
  roles: ['event_mobility_lead'],
  scopes: ['recommendation:approve'],
  modes: ['live'],
};

describe('review desk composition', () => {
  it('runs the staffed desks against the practice snapshot', async () => {
    const repository = new ReviewOperationalRepository();
    const event = await repository.activeEvent('live');
    const snapshot = await repository.snapshot(event!.eventId, principal);
    const findings = snapshot.decisionQueue[0].agentFindings;
    const byCode = Object.fromEntries(findings.map(finding => [finding.agentCode, finding]));

    expect(byCode.atlas.status).toBe('contributed');
    expect(byCode.atlas.citedEvidenceIds.length).toBeGreaterThan(0);
    expect(byCode.aqua.status).toBe('contributed');
    expect(byCode.echo.status).toBe('contributed');
    expect(byCode.sentinel.status).toBe('abstained');
    expect(byCode.phoenix.status).toBe('abstained');
    expect(byCode.nexus.candidateAction).toBe(snapshot.decisionQueue[0].recommendedAction);
  });
});
