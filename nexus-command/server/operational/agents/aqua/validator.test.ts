import { describe, expect, it } from 'vitest';
import type { DetectionEvidence } from '../../detection/rules.js';
import { AQUA_ACTION_TEXT } from './actions.js';
import { emptyToolState, type AquaDraft } from './tools.js';
import { validateAquaDraft } from './validator.js';

const EVIDENCE_ID = '00000000-0000-4000-8000-0000000000cc';

function transit(): DetectionEvidence {
  return {
    evidenceId: EVIDENCE_ID,
    connectorCode: 'auburn-eta-spot-v1',
    sourceEventId: 'veh:1',
    sourceName: 'Tiger Transit',
    summary: 'Two shuttles available for staging',
    observedAt: new Date().toISOString(),
    contentHash: 'hash-cc',
    geometryGeojson: null,
    attributes: { availableUnits: 2 },
  };
}

function draft(overrides: Partial<AquaDraft> = {}): AquaDraft {
  return {
    observation: 'Two Tiger Transit shuttles are visible in the current window.',
    interpretation: 'Lot occupancy is not connected, so a full lot cannot be distinguished from a shuttle delay.',
    confidence: 0.35,
    citedEvidenceIds: [EVIDENCE_ID],
    ...overrides,
  };
}

describe('AQUA draft validator', () => {
  it('accepts a cited draft after the agent read the row', () => {
    const state = emptyToolState();
    state.readIds.add(EVIDENCE_ID);
    state.proposedAction = AQUA_ACTION_TEXT.confirm_lot_shuttle;
    const assessment = validateAquaDraft(draft(), state, [transit()]);
    expect(assessment?.citedEvidenceIds).toEqual([EVIDENCE_ID]);
    expect(assessment?.candidateAction).toBe(AQUA_ACTION_TEXT.confirm_lot_shuttle);
  });

  it('rejects a citation the agent never read', () => {
    expect(validateAquaDraft(draft(), emptyToolState(), [transit()])).toBeNull();
  });

  it('rejects traffic evidence even if it is sitting in the snapshot', () => {
    const traffic: DetectionEvidence = {
      ...transit(),
      evidenceId: '00000000-0000-4000-8000-0000000000dd',
      connectorCode: 'tomtom-traffic-flow-v1',
    };
    const state = emptyToolState();
    state.readIds.add(traffic.evidenceId);
    expect(validateAquaDraft(draft({ citedEvidenceIds: [traffic.evidenceId] }), state, [traffic])).toBeNull();
  });

  it('rejects a draft that tries to change a schedule', () => {
    const state = emptyToolState();
    state.readIds.add(EVIDENCE_ID);
    expect(validateAquaDraft(draft({
      interpretation: 'Change the operator schedule so more shuttles run this hour.',
    }), state, [transit()])).toBeNull();
  });
});
