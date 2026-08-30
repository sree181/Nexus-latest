import { describe, expect, it } from 'vitest';
import type { GraphSnapshot } from '../graphTypes';
import type { Incident, SourceHealth } from '../operationalTypes';
import {
  deskStaffing,
  feedPills,
  lineageStages,
  situationFromIncident,
  walkPath,
} from './wallSelectors';

function node(nodeId: string, nodeType: string, label: string, extras: Partial<{ status: string; flags: string[]; updatedAt: string }> = {}) {
  return {
    nodeId,
    eventId: 'e1',
    mode: 'live' as const,
    nodeType,
    externalKey: nodeId,
    label,
    ownerAgencyId: null,
    sourceId: null,
    authorityUri: null,
    dataClassification: 'operational' as const,
    geometryGeojson: null,
    state: extras.status ? { status: extras.status } : {},
    qualityFlags: extras.flags ?? [],
    validFrom: extras.updatedAt ?? '2026-08-30T14:00:00.000Z',
    validUntil: null,
    active: true,
    version: 1,
    updatedAt: extras.updatedAt ?? '2026-08-30T14:00:00.000Z',
  };
}

function edge(fromNodeId: string, toNodeId: string) {
  return {
    edgeId: `${fromNodeId}:${toNodeId}`,
    eventId: 'e1',
    mode: 'live' as const,
    edgeType: 'supports',
    externalKey: `${fromNodeId}:${toNodeId}`,
    fromNodeId,
    toNodeId,
    directed: true,
    ownerAgencyId: null,
    sourceId: null,
    authorityUri: null,
    dataClassification: 'operational' as const,
    geometryGeojson: null,
    state: {},
    qualityFlags: [],
    validFrom: '2026-08-30T14:00:00.000Z',
    validUntil: null,
    active: true,
    version: 1,
    updatedAt: '2026-08-30T14:00:00.000Z',
  };
}

describe('situationFromIncident', () => {
  it('uses impact when present', () => {
    const incident = {
      title: 'Authoritative mobility observations require command review',
      impact: 'Emergency access at risk on E Samford Ave',
      whyItMatters: 'Authoritative mobility observations require command review.',
    } as Incident;
    expect(situationFromIncident(incident)).toBe('Emergency access at risk on E Samford Ave');
  });

  it('derives one impact sentence and never joins title onto it', () => {
    const incident = {
      title: 'Arrival congestion — remote-lot approach',
      whyItMatters: 'Continued spillback could obstruct the designated emergency-access corridor.',
    } as Incident;
    expect(situationFromIncident(incident)).toBe('Emergency access at risk: remote-lot approach');
    expect(situationFromIncident(incident)).not.toMatch(/ on /);
  });

  it('falls back to title alone when impact cannot be derived', () => {
    const incident = {
      title: 'Arrival congestion — remote-lot approach',
      whyItMatters: 'Authoritative mobility observations require command review for this window.',
    } as Incident;
    expect(situationFromIncident(incident)).toBe('Arrival congestion — remote-lot approach');
  });

  it('uses the empty-state sentence when there is no incident', () => {
    expect(situationFromIncident(null)).toBe('No incident requires a decision');
  });
});

describe('lineageStages', () => {
  const snapshot: GraphSnapshot = {
    eventId: 'e1',
    mode: 'live',
    view: 'decision_lineage',
    asOf: '2026-08-30T14:00:00.000Z',
    generatedAt: '2026-08-30T14:00:00.000Z',
    nodes: [
      node('ev1', 'evidence', 'Sidewalk closed for two blocks on College Street'),
      node('f1', 'finding', 'ATLAS · contributed'),
      node('f2', 'finding', 'ATLAS · contributed'),
      node('f3', 'finding', 'AQUA · contributed'),
      node('inc1', 'incident', 'Queue on the remote-lot approach', { status: 'active' }),
      node('r1', 'recommendation', 'Stage the next two shuttles'),
      node('r2', 'recommendation', 'Stage the next two shuttles'),
      node('r3', 'recommendation', 'Stage the next two shuttles'),
      node('d1', 'decision', 'Approve · Jordan Smith', { flags: ['current'] }),
      node('c1', 'commitment', 'Stage shuttles', { flags: ['current'] }),
    ],
    edges: [
      edge('ev1', 'f1'),
      edge('f1', 'inc1'),
      edge('inc1', 'r1'),
      edge('r1', 'd1'),
      edge('d1', 'c1'),
    ],
  };

  it('groups findings by agency and never says contributed', () => {
    const findings = lineageStages(snapshot).find(stage => stage.key === 'finding')!;
    expect(findings.allItems.map(item => item.title)).toEqual(['ATLAS', 'AQUA']);
    expect(findings.allItems[0].count).toBe(2);
    expect(findings.allItems.some(item => /contributed/i.test(item.title))).toBe(false);
  });

  it('deduplicates recommendations and returns a count', () => {
    const recs = lineageStages(snapshot).find(stage => stage.key === 'recommendation')!;
    expect(recs.allItems).toHaveLength(1);
    expect(recs.allItems[0].count).toBe(3);
    expect(recs.count).toBe(3);
  });

  it('walks parents from the decision card', () => {
    const stages = lineageStages(snapshot);
    const path = walkPath('d1', stages);
    expect([...path].sort()).toEqual(['c1', 'd1', 'ev1', 'f1', 'inc1', 'r1']);
  });
});

describe('feed and desk selectors', () => {
  it('marks feed freshness from lastSeenAt', () => {
    const now = Date.parse('2026-08-30T14:10:00.000Z');
    const sources = [
      { name: 'TomTom', lastEventObservedAt: '2026-08-30T14:09:40.000Z', connectionStatus: 'connected', status: 'healthy' },
      { name: 'NWS', lastSuccessAt: '2026-08-30T14:08:00.000Z', connectionStatus: 'connected', status: 'healthy' },
      { name: 'Parking', lastSuccessAt: null, lastEventObservedAt: null, connectionStatus: 'permission_required', status: 'unavailable' },
    ] as SourceHealth[];
    const pills = feedPills(sources, now);
    expect(pills.map(item => item.tone)).toEqual(['ok', 'stale', 'danger']);
  });

  it('treats contributed desks as staffed', () => {
    const desks = deskStaffing([
      { agentCode: 'atlas', status: 'contributed' },
      { agentCode: 'aqua', status: 'abstained' },
    ]);
    expect(desks.filter(desk => desk.staffed).map(desk => desk.code)).toEqual(['atlas']);
    expect(desks).toHaveLength(6);
    expect(desks.find(desk => desk.code === 'aqua')?.role).toBe('Parking');
    expect(desks.find(desk => desk.code === 'forge')?.role).toBe('Roads');
  });
});
