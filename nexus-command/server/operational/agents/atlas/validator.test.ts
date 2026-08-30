import { describe, expect, it } from 'vitest';
import type { DetectionEvidence } from '../../detection/rules.js';
import { ATLAS_ACTION_TEXT } from './actions.js';
import { emptyToolState, type AtlasDraft } from './tools.js';
import { validateAtlasDraft } from './validator.js';

const EVIDENCE_ID = '00000000-0000-4000-8000-0000000000aa';

function traffic(): DetectionEvidence {
  return {
    evidenceId: EVIDENCE_ID,
    connectorCode: 'aldot-algo-traffic-v1',
    sourceEventId: 'event:1',
    sourceName: 'ALGO',
    summary: 'I-85 southbound heavy congestion',
    observedAt: new Date().toISOString(),
    contentHash: 'hash-aa',
    geometryGeojson: null,
    attributes: { layer: 'travel_time', congestionLevel: 'Heavy', currentSpeedMph: 22, freeFlowSpeedMph: 65 },
  };
}

function draft(overrides: Partial<AtlasDraft> = {}): AtlasDraft {
  return {
    observation: 'One corridor is reporting heavy congestion on the I-85 Auburn approach.',
    interpretation: 'Approach capacity is already reduced, so a change here has little headroom.',
    confidence: 0.64,
    citedEvidenceIds: [EVIDENCE_ID],
    ...overrides,
  };
}

describe('ATLAS draft validator', () => {
  it('accepts a cited draft after the agent read the row', () => {
    const state = emptyToolState();
    state.readIds.add(EVIDENCE_ID);
    state.proposedAction = ATLAS_ACTION_TEXT.confirm_corridor;
    const assessment = validateAtlasDraft(draft(), state, [traffic()]);
    expect(assessment?.citedEvidenceIds).toEqual([EVIDENCE_ID]);
    expect(assessment?.candidateAction).toBe(ATLAS_ACTION_TEXT.confirm_corridor);
  });

  it('rejects a citation the agent never read', () => {
    const state = emptyToolState();
    expect(validateAtlasDraft(draft(), state, [traffic()])).toBeNull();
  });

  it('rejects parking evidence even if it is sitting in the snapshot', () => {
    const parking: DetectionEvidence = {
      ...traffic(),
      evidenceId: '00000000-0000-4000-8000-0000000000bb',
      connectorCode: 'auburn-parking-occupancy-v1',
    };
    const state = emptyToolState();
    state.readIds.add(parking.evidenceId);
    expect(validateAtlasDraft(draft({ citedEvidenceIds: [parking.evidenceId] }), state, [parking])).toBeNull();
  });

  it('rejects a draft that tries to change a signal', () => {
    const state = emptyToolState();
    state.readIds.add(EVIDENCE_ID);
    expect(validateAtlasDraft(draft({
      interpretation: 'Change the signal plan at College and Magnolia to flush the queue.',
    }), state, [traffic()])).toBeNull();
  });
});
