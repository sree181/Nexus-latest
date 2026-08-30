import { describe, expect, it } from 'vitest';
import type { DetectionEvidence } from '../../detection/rules.js';
import { emptyToolState, executeAtlasTool } from './tools.js';

const SPEED_ID = '00000000-0000-4000-8000-0000000000dd';
const PARKING_ID = '00000000-0000-4000-8000-0000000000ee';

function speed(): DetectionEvidence {
  return {
    evidenceId: SPEED_ID,
    connectorCode: 'tomtom-traffic-flow-v1',
    sourceEventId: 'seg:1',
    sourceName: 'TomTom',
    summary: 'College Street 18 mph',
    observedAt: new Date().toISOString(),
    contentHash: 'hash-dd',
    geometryGeojson: null,
    attributes: { currentSpeedMph: 18, freeFlowSpeedMph: 35 },
  };
}

function parking(): DetectionEvidence {
  return {
    evidenceId: PARKING_ID,
    connectorCode: 'auburn-parking-occupancy-v1',
    sourceEventId: 'lot:1',
    sourceName: 'Parking',
    summary: 'Should never be visible to ATLAS tools',
    observedAt: new Date().toISOString(),
    contentHash: 'hash-ee',
    geometryGeojson: null,
    attributes: { occupancy: 0.9 },
  };
}

describe('ATLAS tools', () => {
  it('lists only permitted connectors', () => {
    const state = emptyToolState();
    const result = executeAtlasTool('list_atlas_evidence', {}, [speed(), parking()], state) as { count: number; evidence: Array<{ id: string }> };
    expect(result.count).toBe(1);
    expect(result.evidence[0].id).toBe(SPEED_ID);
    expect(state.readIds.has(PARKING_ID)).toBe(false);
  });

  it('refuses get_evidence on a parking row', () => {
    const result = executeAtlasTool('get_evidence', { id: PARKING_ID }, [speed(), parking()], emptyToolState()) as { error: string };
    expect(result.error).toBe('unknown_or_forbidden');
  });

  it('searches operator-loaded policies without treating them as evidence', () => {
    const policies = [{
      id: 'policy:state-algo',
      title: 'ALDOT traveler messages are state-operated',
      jurisdiction: 'state' as const,
      source: 'Operating note',
      body: 'Local drafted language must not contradict what ALDOT is already displaying on a message sign.',
    }];
    const result = executeAtlasTool('search_policies', { query: 'ALDOT' }, [speed()], emptyToolState(), policies) as {
      count: number;
      policies: Array<{ id: string; evidence: boolean }>;
    };
    expect(result.count).toBe(1);
    expect(result.policies[0].id).toBe('policy:state-algo');
    expect(result.policies[0].evidence).toBe(false);
  });

  it('compares probe speed to free flow', () => {
    const result = executeAtlasTool('compare_to_freeflow', { id: SPEED_ID }, [speed()], emptyToolState()) as {
      comparable: boolean;
      ratio: number;
      belowReviewThreshold: boolean;
    };
    expect(result.comparable).toBe(true);
    expect(result.ratio).toBeCloseTo(18 / 35, 3);
    expect(result.belowReviewThreshold).toBe(true);
  });
});
