import { describe, expect, it } from 'vitest';
import { composeDeskFindings } from './orchestrator.js';
import { agentDesks } from './desks.js';
import type { DetectionEvidence, DetectionMatch } from '../detection/rules.js';

const GAMEDAY_DESKS = ['atlas', 'sentinel', 'phoenix', 'aqua', 'echo', 'nexus'];

function evidence(overrides: Partial<DetectionEvidence> & { evidenceId: string; connectorCode: string }): DetectionEvidence {
  return {
    sourceEventId: `src:${overrides.evidenceId}`,
    sourceName: 'Test source',
    summary: 'Test observation',
    observedAt: new Date().toISOString(),
    contentHash: `hash:${overrides.evidenceId}`,
    geometryGeojson: null,
    attributes: {},
    ...overrides,
  };
}

function match(overrides: Partial<DetectionMatch['rule']> = {}): DetectionMatch {
  const primary = evidence({ evidenceId: '00000000-0000-4000-8000-000000000001', connectorCode: 'aldot-algo-traffic-v1' });
  return {
    rule: {
      packCode: 'sec_gameday',
      ruleCode: 'algo-crash',
      connectorCode: 'aldot-algo-traffic-v1',
      agentCode: 'atlas',
      name: 'ALDOT-reported crash',
      whyItMatters: 'A crash narrows a corridor.',
      severity: 'high',
      affectedServices: ['traffic'],
      constraints: ['Preserve emergency corridor'],
      playbook: {
        recommendedAction: 'Confirm the crash and decide whether local detour or messaging support is required.',
        expectedEffect: 'Agencies acknowledge.',
        limitations: 'ALGO record only.',
        approvals: [],
        commitments: [],
      },
      ...overrides,
    },
    externalKey: 'crash-1',
    title: 'Crash on the Auburn approach',
    whatChanged: 'ALDOT reported a crash.',
    severity: 'high',
    primary,
    evidence: [primary],
    evidenceHash: 'snapshot-hash',
  };
}

describe('desk contract', () => {
  it('gives every desk a declared input list and a stated boundary', () => {
    for (const desk of agentDesks) {
      expect(desk.allowedConnectors.length, `${desk.code} declares no inputs`).toBeGreaterThan(0);
      expect(desk.boundary, `${desk.code} states no boundary`).not.toBe('');
      expect(desk.mission).not.toBe('');
    }
  });

  it('never lets a desk read a connector it has not declared', () => {
    const foreign = evidence({ evidenceId: '00000000-0000-4000-8000-0000000000ff', connectorCode: 'auburn-parking-occupancy-v1' });
    const atlas = agentDesks.find(desk => desk.code === 'atlas')!;
    // ATLAS is handed parking evidence directly; it must not produce a citation from it.
    const assessment = atlas.assess([foreign], { match: match(), snapshot: [foreign], liveConnectors: [], now: Date.now() });
    expect(assessment).toBeNull();
  });

  it('requires a contributing desk to cite evidence', () => {
    const composition = composeDeskFindings({
      staffedAgentCodes: GAMEDAY_DESKS,
      match: match(),
      snapshot: [
        evidence({
          evidenceId: '00000000-0000-4000-8000-000000000002',
          connectorCode: 'aldot-algo-traffic-v1',
          attributes: { layer: 'travel_time', congestionLevel: 'Heavy' },
          summary: 'I-85 southbound heavy congestion',
        }),
      ],
      liveConnectors: ['aldot-algo-traffic-v1'],
    });

    for (const finding of composition.findings) {
      if (finding.status === 'contributed' && finding.agentCode !== 'nexus') {
        expect(finding.citedEvidenceIds.length, `${finding.agentCode} contributed without citing`).toBeGreaterThan(0);
      }
      if (finding.status === 'abstained') {
        expect(finding.citedEvidenceIds).toEqual([]);
      }
    }
  });
});

describe('composition', () => {
  /**
   * The acceptance case: two desks contribute on real evidence, the parking desk is silent because
   * no occupancy feed exists, and the two contributors disagree on one point.
   */
  it('names contributors, reports the silent desks, and records dissent', () => {
    const snapshot = [
      evidence({
        evidenceId: '00000000-0000-4000-8000-000000000010',
        connectorCode: 'aldot-algo-traffic-v1',
        attributes: { layer: 'travel_time', congestionLevel: 'Heavy' },
        summary: 'I-85 southbound at Exit 51 heavy congestion',
      }),
      evidence({
        evidenceId: '00000000-0000-4000-8000-000000000011',
        connectorCode: 'aldot-algo-traffic-v1',
        attributes: { layer: 'message_sign', pages: ['CRASH AHEAD / USE CAUTION'] },
        summary: 'I-85 message sign southbound Auburn',
      }),
    ];

    const composition = composeDeskFindings({
      staffedAgentCodes: GAMEDAY_DESKS,
      match: match(),
      snapshot,
      liveConnectors: ['aldot-algo-traffic-v1'],
    });

    expect(composition.contributors).toContain('atlas');
    expect(composition.contributors).toContain('echo');

    const aqua = composition.silent.find(entry => entry.agentCode === 'aqua');
    expect(aqua, 'AQUA should be silent with no transit or parking feed').toBeDefined();
    expect(aqua!.reason).toMatch(/Tiger Transit|parking/i);

    // ECHO qualifies ATLAS: ALDOT is already posting traveler text on this approach.
    const dissent = composition.conflicts.find(conflict => conflict.fromAgentCode === 'echo');
    expect(dissent, 'ECHO should dissent when signs already display text').toBeDefined();
    expect(dissent!.withAgentCode).toBe('atlas');
    expect(dissent!.basis).toContain('CRASH AHEAD');

    const nexus = composition.findings.find(finding => finding.agentCode === 'nexus')!;
    expect(nexus.observation).toContain('ATLAS');
    expect(nexus.observation).toContain('ECHO');
    expect(nexus.observation).toContain('AQUA');
    expect(nexus.interpretation).toMatch(/do not fully agree/);
    expect(nexus.limitations).toContain('AQUA');
    // NEXUS reconciles; it does not author a different action than the approved playbook.
    expect(nexus.candidateAction).toBe(match().rule.playbook.recommendedAction);
  });

  it('produces one finding per staffed desk plus the composer', () => {
    const composition = composeDeskFindings({
      staffedAgentCodes: GAMEDAY_DESKS,
      match: match(),
      snapshot: [],
      liveConnectors: [],
    });
    // Five staffed domain desks (nexus is the composer, forge is not staffed for game day).
    expect(composition.findings).toHaveLength(6);
    expect(composition.findings.filter(finding => finding.agentCode === 'nexus')).toHaveLength(1);
  });

  it('states plainly when no desk could evaluate the incident', () => {
    const composition = composeDeskFindings({
      staffedAgentCodes: GAMEDAY_DESKS,
      match: match(),
      snapshot: [],
      liveConnectors: [],
    });
    expect(composition.contributors).toEqual([]);
    const nexus = composition.findings.find(finding => finding.agentCode === 'nexus')!;
    expect(nexus.status).toBe('abstained');
    expect(nexus.observation).toMatch(/No staffed desk could evaluate/);
  });

  it('has PHOENIX flag an emergency-corridor constraint it cannot verify', () => {
    const composition = composeDeskFindings({
      staffedAgentCodes: GAMEDAY_DESKS,
      match: match(),
      snapshot: [
        evidence({
          evidenceId: '00000000-0000-4000-8000-000000000020',
          connectorCode: 'coa-road-closures-v1',
          attributes: { kind: 'closure' },
          summary: 'Published closure on Donahue Drive',
        }),
      ],
      liveConnectors: ['coa-road-closures-v1'],
    });

    const phoenix = composition.findings.find(finding => finding.agentCode === 'phoenix')!;
    expect(phoenix.status).toBe('contributed');
    const conflict = composition.conflicts.find(item => item.fromAgentCode === 'phoenix');
    expect(conflict, 'PHOENIX should flag the unverifiable corridor').toBeDefined();
    expect(conflict!.basis).toMatch(/No emergency-access feed is connected/);
  });

  it('keeps a desk silent rather than guessing when its feed is absent', () => {
    const composition = composeDeskFindings({
      staffedAgentCodes: ['aqua', 'nexus'],
      match: match(),
      snapshot: [
        evidence({ evidenceId: '00000000-0000-4000-8000-000000000030', connectorCode: 'aldot-algo-traffic-v1' }),
      ],
      liveConnectors: ['aldot-algo-traffic-v1'],
    });
    const aqua = composition.findings.find(finding => finding.agentCode === 'aqua')!;
    expect(aqua.status).toBe('abstained');
    expect(aqua.candidateAction).toMatch(/silent by design/);
  });
});
