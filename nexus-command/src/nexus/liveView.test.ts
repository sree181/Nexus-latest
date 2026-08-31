import { describe, expect, it } from 'vitest';
import type { LiveBundle } from './liveStore';
import { buildLiveView, findingStance, hhmm, shortHash } from './liveView';
import type { AgentFinding, OperationalSnapshot, Recommendation } from '../operationalTypes';

const finding = (over: Partial<AgentFinding> = {}): AgentFinding => ({
  agentCode: 'atlas',
  agentName: 'ATLAS',
  status: 'contributed',
  observation: 'Wire Rd segment speed 11 mph against a 28 mph free flow.',
  interpretation: 'Approach capacity is reduced.',
  candidateAction: 'Confirm the corridor.',
  confidence: 0.68,
  limitations: 'Probe speed only.',
  citedEvidenceIds: ['aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee1'],
  conflicts: [],
  createdAt: '2026-08-31T09:31:00Z',
  ...over,
});

const rec: Recommendation = {
  recommendationId: 'r1',
  incidentId: 'i1',
  mode: 'live',
  version: 3,
  state: 'awaiting_approval',
  priority: 'high',
  whatChanged: 'Closure posted.',
  whyItMatters: 'The hospital route now runs through a single signal.',
  recommendedAction: 'Publish the Donahue Drive detour.',
  expectedEffect: 'Clears the approach queue.',
  limitations: 'No parking feed.',
  constraints: [],
  evidenceSnapshotHash: '9f3ac17deadbeef',
  evidence: [{
    evidenceId: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeee1',
    sourceId: 's1',
    sourceName: 'TomTom Traffic Flow',
    observedAt: '2026-08-31T09:39:00Z',
    receivedAt: '2026-08-31T09:39:01Z',
    summary: 'Wire Rd WB segment · 11 mph',
    qualityFlags: [],
    attributes: {},
  }],
  approvalRequirements: [],
  agentFindings: [
    finding(),
    finding({
      agentCode: 'aqua', agentName: 'AQUA', status: 'abstained',
      observation: '', limitations: 'Parking occupancy needs a partner agreement.',
      citedEvidenceIds: [], confidence: null,
    }),
    finding({
      agentCode: 'phoenix', agentName: 'PHOENIX',
      observation: 'Hospital route degrades.',
      conflicts: [{ withAgentCode: 'atlas', concern: 'Detour puts the hospital route through a single signal.', basis: 'No emergency-access feed.' }],
    }),
  ],
  generatedBy: { model: 'nexus-composer', version: 'v1' },
  expiresAt: '2026-08-31T10:12:00Z',
  createdAt: '2026-08-31T09:41:00Z',
  updatedAt: '2026-08-31T09:41:00Z',
};

const snapshot: OperationalSnapshot = {
  event: {
    eventId: 'e1', mode: 'live', eventType: 'road_closure', name: 'Auburn Mobility Operations',
    phase: 'steady_state', status: 'active', startsAt: '2026-08-31T08:00:00Z', endsAt: null,
    locationName: 'Auburn', commandOwner: null, scenarioPackCode: 'road_closure', version: 1, updatedAt: '2026-08-31T09:00:00Z',
  },
  incidents: [{
    incidentId: 'i1', eventId: 'e1', mode: 'live', title: 'Wire Rd WB at Shug Jordan Pkwy',
    whatChanged: 'City closure posted.', whyItMatters: 'The hospital route now runs through a single signal.',
    severity: 'high', status: 'active', commandOwner: null,
    locationGeojson: { type: 'Point', coordinates: [-85.515, 32.585] },
    affectedServices: ['traffic', 'emergency_access', 'transit'], constraints: [],
    detectedAt: '2026-08-31T09:12:00Z', resolvedAt: null, version: 1, updatedAt: '2026-08-31T09:12:00Z',
  }],
  decisionQueue: [rec],
  commitments: [],
  sources: [{
    sourceId: 's1', sourceCode: 'coa', name: 'City closures', ownerAgencyName: 'City',
    status: 'healthy', lastSuccessAt: '2026-08-31T09:40:00Z', lastEventObservedAt: null,
    lagSeconds: 12, staleAfterSeconds: 300, errorCategory: null, connectionStatus: 'connected',
  }, {
    sourceId: 's2', sourceCode: 'park', name: 'Parking occupancy', ownerAgencyName: 'City',
    status: 'unavailable', lastSuccessAt: null, lastEventObservedAt: null,
    lagSeconds: null, staleAfterSeconds: 300, errorCategory: null, connectionStatus: 'permission_required',
  }],
  observations: [],
};

const bundle = (over: Partial<LiveBundle> = {}): LiveBundle => ({
  loading: false, error: null, noWindow: false, status: { status: 'operational', mode: 'live', checkedAt: '', database: 'review_repository', sourceSummary: { healthy: 1, delayed: 0, unavailable: 1, unverified: 0 }, message: '' },
  principal: { principalId: 'p', externalSubject: 'x', displayName: 'J. Ruffin', agencyId: 'a', agencyName: 'City of Auburn', roles: ['command_lead'], scopes: ['decision:write'], modes: ['live'] },
  snapshot, graph: null, packs: [], fetchedAt: Date.now(), ...over,
});

describe('live view', () => {
  it('keeps dissent distinct from contributed and never treats silence as agreement', () => {
    const view = buildLiveView(bundle());
    const phoenix = view.desks.find(d => d.code === 'phoenix')!;
    const aqua = view.desks.find(d => d.code === 'aqua')!;
    const echo = view.desks.find(d => d.code === 'echo')!;
    expect(phoenix.statusLabel).toBe('Dissent');
    expect(aqua.statusLabel).toBe('Abstained');
    expect(echo.statusLabel).toBe('Abstained');
    expect(echo.line).toMatch(/Not staffed|No permitted/i);
    expect(view.dissentCount).toBe(1);
    expect(view.awaiting).toBe(true);
    expect(view.canDecide).toBe(true);
  });

  it('does not invent commitments before a decision is written', () => {
    const view = buildLiveView(bundle());
    expect(view.commitmentPreview).toEqual([]);
    expect(view.recAction).toBe('Publish the Donahue Drive detour.');
    expect(view.packLine).toBe('pack road_closure');
    expect(view.evidenceCount).toBe(1);
    expect(view.composeLine).toMatch(/from 2 findings/);
    const commitments = view.lineage.columns.find(col => col.key === 'commitment')!;
    expect(commitments.cards.every(card => card.id === 'c-none' || !card.id.startsWith('c-CMT'))).toBe(true);
    expect(commitments.cards.some(card => /none yet/i.test(card.title) || card.id === 'c-none')).toBe(true);
    expect(view.lineage.nodes['d-gate']).toBeTruthy();
    expect(Object.keys(view.lineage.nodes).some(id => id.startsWith('d-') && id !== 'd-gate')).toBe(false);
  });

  it('states the empty window plainly', () => {
    const view = buildLiveView(bundle({ noWindow: true, snapshot: null }));
    expect(view.noWindow).toBe(true);
    expect(view.incidentTitle).toBe('No incident requires a decision');
    expect(view.canDecide).toBe(false);
  });
});

describe('finding stance', () => {
  it('promotes a conflict to dissent', () => {
    expect(findingStance(finding({ conflicts: [{ withAgentCode: 'atlas', concern: 'x', basis: 'y' }] }))).toBe('dissent');
  });
});

describe('formatters', () => {
  it('shortens hashes without inventing them', () => {
    expect(shortHash('9f3ac17deadbeef')).toBe('9f3a…eef');
    expect(shortHash('')).toBe('—');
  });
  it('renders a clock from an iso stamp', () => {
    expect(hhmm('2026-08-31T09:12:00Z')).toMatch(/^\d{2}:\d{2}$/);
  });
});
