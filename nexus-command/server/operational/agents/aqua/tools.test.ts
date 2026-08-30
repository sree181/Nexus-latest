import { describe, expect, it } from 'vitest';
import type { DetectionEvidence } from '../../detection/rules.js';
import { emptyToolState, executeAquaTool } from './tools.js';

const TRANSIT_ID = '00000000-0000-4000-8000-0000000000aa';
const SPEED_ID = '00000000-0000-4000-8000-0000000000bb';

function shuttle(): DetectionEvidence {
  return {
    evidenceId: TRANSIT_ID,
    connectorCode: 'auburn-eta-spot-v1',
    sourceEventId: 'veh:1',
    sourceName: 'Tiger Transit',
    summary: 'Two shuttles available for staging',
    observedAt: new Date().toISOString(),
    contentHash: 'hash-aa',
    geometryGeojson: null,
    attributes: { availableUnits: 2, currentDelayMinutes: 4 },
  };
}

function speed(): DetectionEvidence {
  return {
    evidenceId: SPEED_ID,
    connectorCode: 'tomtom-traffic-flow-v1',
    sourceEventId: 'seg:1',
    sourceName: 'TomTom',
    summary: 'Should never be visible to AQUA tools',
    observedAt: new Date().toISOString(),
    contentHash: 'hash-bb',
    geometryGeojson: null,
    attributes: { currentSpeedMph: 18, freeFlowSpeedMph: 35 },
  };
}

describe('AQUA tools', () => {
  it('lists only permitted connectors', () => {
    const state = emptyToolState();
    const result = executeAquaTool('list_aqua_evidence', {}, [shuttle(), speed()], state) as { count: number; evidence: Array<{ id: string }> };
    expect(result.count).toBe(1);
    expect(result.evidence[0].id).toBe(TRANSIT_ID);
    expect(state.readIds.has(SPEED_ID)).toBe(false);
  });

  it('refuses get_evidence on a traffic row', () => {
    const result = executeAquaTool('get_evidence', { id: SPEED_ID }, [shuttle(), speed()], emptyToolState()) as { error: string };
    expect(result.error).toBe('unknown_or_forbidden');
  });

  it('searches operator-loaded policies without treating them as evidence', () => {
    const policies = [{
      id: 'policy:dept-parking',
      title: 'Lot policy stays with Parking & Transit',
      jurisdiction: 'department' as const,
      source: 'Operating note',
      body: 'Parking & Transit sets lot hours, remote-lot use, and shuttle schedules. AQUA may ask them to confirm state.',
    }];
    const result = executeAquaTool('search_policies', { query: 'Parking' }, [shuttle()], emptyToolState(), policies) as {
      count: number;
      policies: Array<{ id: string; evidence: boolean }>;
    };
    expect(result.count).toBe(1);
    expect(result.policies[0].id).toBe('policy:dept-parking');
    expect(result.policies[0].evidence).toBe(false);
  });
});
