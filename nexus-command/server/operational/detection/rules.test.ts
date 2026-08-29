import { describe, expect, it } from 'vitest';
import {
  evaluateRules,
  hasPredicate,
  isInEffect,
  type DetectionEvidence,
  type DetectionRuleDefinition,
  type Playbook,
} from './rules.js';

const NOW = Date.parse('2026-09-12T18:00:00.000Z');
const HOUR = 60 * 60 * 1000;

const playbook: Playbook = {
  recommendedAction: 'Review and decide.',
  expectedEffect: 'Agencies acknowledge.',
  limitations: 'Public record only.',
  approvals: [{ agencyCode: 'event-mobility-command', roleCode: 'event_mobility_lead' }],
  commitments: [{ agencyCode: 'city-traffic', requestedOutcome: 'Confirm.', verificationRule: 'Field report', dueInMinutes: 60 }],
};

function rule(overrides: Partial<DetectionRuleDefinition> & Pick<DetectionRuleDefinition, 'ruleCode' | 'connectorCode'>): DetectionRuleDefinition {
  return {
    packCode: 'sec_gameday',
    agentCode: 'atlas',
    name: overrides.ruleCode,
    whyItMatters: 'It matters.',
    severity: 'medium',
    affectedServices: ['traffic'],
    constraints: ['No traffic-signal control from Nexus'],
    playbook,
    ...overrides,
  };
}

function evidence(overrides: Partial<DetectionEvidence> & Pick<DetectionEvidence, 'evidenceId' | 'connectorCode' | 'attributes'>): DetectionEvidence {
  return {
    sourceEventId: overrides.evidenceId,
    sourceName: 'Test source',
    summary: 'Test observation',
    observedAt: new Date(NOW - 5 * 60 * 1000).toISOString(),
    contentHash: `hash-${overrides.evidenceId}`,
    geometryGeojson: { type: 'Point', coordinates: [-85.49, 32.6] },
    ...overrides,
  };
}

const algoRule = rule({ ruleCode: 'algo-crash', connectorCode: 'aldot-algo-traffic-v1', severity: 'high' });
const congestionRule = rule({ ruleCode: 'algo-corridor-congestion', connectorCode: 'aldot-algo-traffic-v1' });
const cityRule = rule({ ruleCode: 'city-restriction-in-effect', connectorCode: 'coa-road-closures-v1' });
const flowRule = rule({ ruleCode: 'tomtom-corridor-degraded', connectorCode: 'tomtom-traffic-flow-v1' });

describe('detection rules', () => {
  it('opens nothing when no authoritative record qualifies', () => {
    const quiet = [
      evidence({
        evidenceId: 'e1',
        connectorCode: 'aldot-algo-traffic-v1',
        attributes: { layer: 'travel_time', algoTravelTimeId: 7, name: 'I-85 Auburn', congestionLevel: 'Unaffected' },
      }),
      evidence({
        evidenceId: 'e2',
        connectorCode: 'aldot-algo-traffic-v1',
        attributes: { layer: 'message_sign', algoSignId: 12, pages: ['DRIVE SAFELY'] },
      }),
    ];
    expect(evaluateRules([algoRule, congestionRule], quiet, NOW)).toEqual([]);
  });

  it('opens one incident per upstream crash and carries its upstream identity', () => {
    const matches = evaluateRules([algoRule], [
      evidence({
        evidenceId: 'e1',
        connectorCode: 'aldot-algo-traffic-v1',
        attributes: {
          layer: 'traffic_event', algoEventId: 2357202, eventType: 'Crash',
          title: 'Crash blocking right lane', subtitle: 'I-85 NB at MM 51', route: 'I-85 NB',
        },
      }),
      evidence({
        evidenceId: 'e2',
        connectorCode: 'aldot-algo-traffic-v1',
        attributes: {
          layer: 'traffic_event', algoEventId: 2357999, eventType: 'Crash',
          title: 'Crash on shoulder', subtitle: 'US-280 EB', route: 'US-280 EB',
        },
      }),
    ], NOW);

    expect(matches).toHaveLength(2);
    expect(matches.map(match => match.externalKey).sort()).toEqual(['algo-event:2357202', 'algo-event:2357999']);
    expect(matches[0].severity).toBe('high');
  });

  it('collapses repeated observations of the same upstream record onto one incident', () => {
    const matches = evaluateRules([algoRule], [
      evidence({
        evidenceId: 'older',
        connectorCode: 'aldot-algo-traffic-v1',
        observedAt: new Date(NOW - 2 * HOUR).toISOString(),
        attributes: { layer: 'traffic_event', algoEventId: 41, eventType: 'Crash', title: 'Crash', subtitle: 'Right lane blocked', route: 'I-85 NB' },
      }),
      evidence({
        evidenceId: 'newer',
        connectorCode: 'aldot-algo-traffic-v1',
        observedAt: new Date(NOW - 10 * 60 * 1000).toISOString(),
        attributes: { layer: 'traffic_event', algoEventId: 41, eventType: 'Crash', title: 'Crash', subtitle: 'All lanes open', route: 'I-85 NB' },
      }),
    ], NOW);

    expect(matches).toHaveLength(1);
    expect(matches[0].primary.evidenceId).toBe('newer');
    expect(matches[0].evidence).toHaveLength(2);
    expect(matches[0].whatChanged).toContain('All lanes open');
  });

  it('ignores a connector whose evidence belongs to a different rule', () => {
    const matches = evaluateRules([cityRule], [
      evidence({
        evidenceId: 'e1',
        connectorCode: 'aldot-algo-traffic-v1',
        attributes: { layer: 'traffic_event', algoEventId: 5, eventType: 'Crash', title: 'Crash', route: 'I-85' },
      }),
    ], NOW);
    expect(matches).toEqual([]);
  });

  it('opens a City restriction only while it is in effect', () => {
    const inEffect = evidence({
      evidenceId: 'city-now',
      connectorCode: 'coa-road-closures-v1',
      sourceEventId: 'closure:abc',
      attributes: {
        kind: 'closure', road: 'Donahue Drive', description: 'Utility work',
        startsAt: NOW - 3 * HOUR, endsAt: NOW + 3 * HOUR,
      },
    });
    const future = evidence({
      evidenceId: 'city-later',
      connectorCode: 'coa-road-closures-v1',
      sourceEventId: 'closure:def',
      attributes: {
        kind: 'closure', road: 'Wire Road', description: 'Paving',
        startsAt: NOW + 20 * HOUR, endsAt: NOW + 30 * HOUR,
      },
    });
    const past = evidence({
      evidenceId: 'city-done',
      connectorCode: 'coa-road-closures-v1',
      sourceEventId: 'closure:ghi',
      attributes: {
        kind: 'closure', road: 'Shug Jordan', description: 'Completed',
        startsAt: NOW - 40 * HOUR, endsAt: NOW - 20 * HOUR,
      },
    });

    const matches = evaluateRules([cityRule], [inEffect, future, past], NOW);
    expect(matches).toHaveLength(1);
    expect(matches[0].externalKey).toBe('city-closure:closure:abc');
    expect(matches[0].title).toContain('Donahue Drive');
  });

  it('opens flow degradation only below the review threshold and with usable confidence', () => {
    const degraded = evidence({
      evidenceId: 'flow-bad',
      connectorCode: 'tomtom-traffic-flow-v1',
      attributes: { pointCode: 'wire-road', pointName: 'Wire Road at Shug Jordan', currentSpeedMph: 12, freeFlowSpeedMph: 45, confidence: 0.95 },
    });
    const healthy = evidence({
      evidenceId: 'flow-ok',
      connectorCode: 'tomtom-traffic-flow-v1',
      attributes: { pointCode: 'college-street', pointName: 'College Street', currentSpeedMph: 33, freeFlowSpeedMph: 40, confidence: 0.95 },
    });
    const unreliable = evidence({
      evidenceId: 'flow-unsure',
      connectorCode: 'tomtom-traffic-flow-v1',
      attributes: { pointCode: 'magnolia', pointName: 'Magnolia Avenue', currentSpeedMph: 5, freeFlowSpeedMph: 35, confidence: 0.2 },
    });

    const matches = evaluateRules([flowRule], [degraded, healthy, unreliable], NOW);
    expect(matches).toHaveLength(1);
    expect(matches[0].externalKey).toBe('tomtom-point:wire-road');
  });

  it('keeps the same upstream point stable across repeated flow readings', () => {
    const attributes = { pointCode: 'wire-road', pointName: 'Wire Road', currentSpeedMph: 10, freeFlowSpeedMph: 45, confidence: 0.9 };
    const matches = evaluateRules([flowRule], [
      evidence({ evidenceId: 'r1', connectorCode: 'tomtom-traffic-flow-v1', sourceEventId: 'flow:wire-road:2026-09-12T17:40', observedAt: new Date(NOW - 20 * 60 * 1000).toISOString(), attributes }),
      evidence({ evidenceId: 'r2', connectorCode: 'tomtom-traffic-flow-v1', sourceEventId: 'flow:wire-road:2026-09-12T17:55', observedAt: new Date(NOW - 5 * 60 * 1000).toISOString(), attributes }),
    ], NOW);
    expect(matches).toHaveLength(1);
  });

  it('raises severity when ALDOT reports heavy corridor congestion', () => {
    const matches = evaluateRules([congestionRule], [
      evidence({
        evidenceId: 'tt',
        connectorCode: 'aldot-algo-traffic-v1',
        attributes: {
          layer: 'travel_time', algoTravelTimeId: 88, name: 'I-85 SB to Exit 51',
          congestionLevel: 'Heavy', averageSpeedMph: 18, estimatedTimeMinutes: 27,
        },
      }),
    ], NOW);
    expect(matches).toHaveLength(1);
    expect(matches[0].severity).toBe('high');
  });

  it('orders matches so the most severe condition is reviewed first', () => {
    const matches = evaluateRules([algoRule, cityRule], [
      evidence({
        evidenceId: 'city',
        connectorCode: 'coa-road-closures-v1',
        sourceEventId: 'block:1',
        attributes: { kind: 'block', road: 'Heisman Drive', description: 'Event staging', startsAt: null, endsAt: null },
      }),
      evidence({
        evidenceId: 'crash',
        connectorCode: 'aldot-algo-traffic-v1',
        attributes: { layer: 'traffic_event', algoEventId: 9, eventType: 'Crash', title: 'Crash', route: 'I-85 NB' },
      }),
    ], NOW);
    expect(matches.map(match => match.rule.ruleCode)).toEqual(['algo-crash', 'city-restriction-in-effect']);
  });

  it('changes the evidence hash when upstream content changes under a stable evidence id', () => {
    const attributes = { layer: 'traffic_event', algoEventId: 3, eventType: 'Crash', title: 'Crash', route: 'I-85' };
    const original = evidence({ evidenceId: 'a', connectorCode: 'aldot-algo-traffic-v1', contentHash: 'content-1', attributes });
    const repeated = evaluateRules([algoRule], [original], NOW);
    const unchanged = evaluateRules([algoRule], [original], NOW);
    expect(repeated[0].evidenceHash).toBe(unchanged[0].evidenceHash);
    expect(repeated[0].evidenceHash).toHaveLength(64);

    const revised = evaluateRules([algoRule], [{ ...original, contentHash: 'content-2' }], NOW);
    expect(revised[0].evidenceHash).not.toBe(repeated[0].evidenceHash);
  });

  it('skips rules whose predicate is not implemented yet', () => {
    expect(hasPredicate('nws-alert-active')).toBe(true);
    expect(hasPredicate('rule-that-does-not-exist')).toBe(false);
  });

  it('treats an unscheduled record as in effect and a window as bounded', () => {
    expect(isInEffect(null, null, NOW)).toBe(true);
    expect(isInEffect(NOW - HOUR, NOW + HOUR, NOW)).toBe(true);
    expect(isInEffect(NOW + HOUR, null, NOW)).toBe(false);
    expect(isInEffect(null, NOW - HOUR, NOW)).toBe(false);
  });
});
