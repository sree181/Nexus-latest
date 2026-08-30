import { describe, expect, it } from 'vitest';
import type { EventLineage } from '../operational/repository.js';
import { projectDecisionLineage } from './projectLineage.js';

const now = '2026-08-30T12:00:00.000Z';

function lineage(): EventLineage {
  return {
    event: {
      eventId: '22222222-2222-4222-8222-222222222222',
      mode: 'live',
      eventType: 'mobility',
      name: 'Auburn Mobility Operations',
      phase: 'arrival',
      status: 'active',
      startsAt: now,
      endsAt: null,
      locationName: 'Auburn',
      commandOwner: {
        principalId: '11111111-1111-4111-8111-111111111111',
        displayName: 'Jordan Smith',
        agencyId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        agencyName: 'Command',
        roleCode: 'event_mobility_lead',
      },
      scenarioPackCode: 'sec_gameday',
      version: 1,
      updatedAt: now,
    },
    incidents: [{
      incidentId: '33333333-3333-4333-8333-333333333333',
      eventId: '22222222-2222-4222-8222-222222222222',
      mode: 'live',
      title: 'Arrival congestion',
      whatChanged: 'Speed dropped',
      whyItMatters: 'Emergency access',
      severity: 'high',
      status: 'active',
      commandOwner: null,
      locationGeojson: null,
      affectedServices: ['traffic'],
      constraints: [],
      detectedAt: now,
      resolvedAt: null,
      version: 1,
      updatedAt: now,
    }],
    recommendations: [{
      recommendationId: '44444444-4444-4444-8444-444444444444',
      incidentId: '33333333-3333-4333-8333-333333333333',
      mode: 'live',
      version: 1,
      state: 'awaiting_approval',
      priority: 'high',
      whatChanged: 'Speed dropped',
      whyItMatters: 'Emergency access',
      recommendedAction: 'Activate the remote-lot plan',
      expectedEffect: 'Queue eases',
      limitations: 'No signal control',
      constraints: [],
      evidenceSnapshotHash: 'abc12345',
      evidence: [{
        evidenceId: '55555555-5555-4555-8555-555555555551',
        sourceId: '66666666-6666-4666-8666-666666666661',
        sourceName: 'TomTom',
        observedAt: now,
        receivedAt: now,
        summary: 'Approach below threshold',
        qualityFlags: [],
        attributes: {},
      }],
      approvalRequirements: [],
      agentFindings: [{
        agentCode: 'atlas',
        agentName: 'ATLAS',
        status: 'contributed',
        observation: 'Heavy congestion',
        interpretation: 'Little headroom',
        candidateAction: 'Confirm the corridor',
        confidence: 0.7,
        limitations: 'Probe only',
        citedEvidenceIds: ['55555555-5555-4555-8555-555555555551'],
        conflicts: [],
        createdAt: now,
      }],
      generatedBy: { model: 'rule', version: 'v1' },
      expiresAt: now,
      createdAt: now,
      updatedAt: now,
    }],
    decisions: [{
      decisionId: '77777777-7777-4777-8777-777777777771',
      recommendationId: '44444444-4444-4444-8444-444444444444',
      recommendationVersion: 1,
      action: 'approve',
      actor: {
        principalId: '11111111-1111-4111-8111-111111111111',
        displayName: 'Jordan Smith',
        agencyId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        agencyName: 'Command',
        roleCode: 'event_mobility_lead',
      },
      reasonCode: 'approved',
      comment: null,
      decidedAt: now,
    }],
    commitments: [{
      commitmentId: '88888888-8888-4888-8888-888888888881',
      incidentId: '33333333-3333-4333-8333-333333333333',
      recommendationId: '44444444-4444-4444-8444-444444444444',
      decisionId: '77777777-7777-4777-8777-777777777771',
      mode: 'live',
      ownerAgencyId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      ownerAgencyName: 'Parking',
      assignee: null,
      requestedOutcome: 'Stage two shuttles',
      state: 'requested',
      dueAt: now,
      blocker: null,
      verificationRule: 'Transit feed confirms staged units',
      version: 1,
      updatedAt: now,
    }],
  };
}

describe('projectDecisionLineage', () => {
  it('builds the evidence → finding → incident → recommendation → decision → commitment chain', () => {
    const snapshot = projectDecisionLineage(lineage(), now);
    const types = snapshot.nodes.map(node => node.nodeType).sort();
    expect(types).toEqual(['commitment', 'decision', 'evidence', 'finding', 'incident', 'recommendation']);
    expect(snapshot.edges.some(edge => edge.edgeType === 'supports')).toBe(true);
    expect(snapshot.edges.some(edge => edge.edgeType === 'triggered')).toBe(true);
    expect(snapshot.edges.some(edge => edge.edgeType === 'approved')).toBe(true);
    expect(snapshot.edges.some(edge => edge.edgeType === 'assigned')).toBe(true);
  });

  it('flags earlier decisions as historical when a later one exists', () => {
    const source = lineage();
    source.decisions.push({
      ...source.decisions[0],
      decisionId: '77777777-7777-4777-8777-777777777772',
      action: 'request_revision',
      decidedAt: '2026-08-30T11:00:00.000Z',
    });
    const snapshot = projectDecisionLineage(source, now);
    const decisions = snapshot.nodes.filter(node => node.nodeType === 'decision');
    expect(decisions.find(node => node.nodeId.endsWith('7772'))?.qualityFlags).toContain('historical');
    expect(decisions.find(node => node.nodeId.endsWith('7771'))?.qualityFlags).toContain('current');
  });

  it('does not invent a verification node until a commitment is verified', () => {
    expect(projectDecisionLineage(lineage(), now).nodes.some(node => node.nodeType === 'verification')).toBe(false);
    const verified = lineage();
    verified.commitments[0].state = 'verified';
    expect(projectDecisionLineage(verified, now).nodes.some(node => node.nodeType === 'verification')).toBe(true);
  });
});
