import type { DetectionEvidence } from '../../detection/rules.js';
import type { DeskAssessment } from '../desks.js';
import { AQUA_ALLOWED_CONNECTORS } from '../desks.js';
import { AQUA_ACTION_TEXT } from './actions.js';
import type { AquaDraft, AquaToolState } from './tools.js';

const FORBIDDEN = [
  /change (the |a )?(operator )?schedule/i,
  /rewrite .{0,40}(schedule|policy)/i,
  /(open|close) (the |a )?(lot|garage)/i,
  /waive .{0,40}(fee|policy)/i,
  /dispatch/i,
  /i (will|am going to) (open|close|dispatch|change)/i,
];

const ALLOWED_ACTIONS = new Set(Object.values(AQUA_ACTION_TEXT));

export function validateAquaDraft(
  draft: AquaDraft | null,
  state: AquaToolState,
  visible: DetectionEvidence[],
): DeskAssessment | null {
  if (!draft) return null;

  const allowedIds = new Set(
    visible
      .filter(item => item.connectorCode !== null
        && (AQUA_ALLOWED_CONNECTORS as readonly string[]).includes(item.connectorCode))
      .map(item => item.evidenceId),
  );

  const observation = draft.observation.trim();
  const interpretation = draft.interpretation.trim();
  if (observation.length < 12 || interpretation.length < 12) return null;
  if (observation.length > 600 || interpretation.length > 600) return null;
  if (!Number.isFinite(draft.confidence) || draft.confidence < 0.2 || draft.confidence > 0.85) return null;

  const cited = [...new Set(draft.citedEvidenceIds)].slice(0, 12);
  if (!cited.length) return null;
  if (cited.some(id => !allowedIds.has(id))) return null;
  if (cited.some(id => !state.readIds.has(id))) return null;

  const action = state.proposedAction && ALLOWED_ACTIONS.has(state.proposedAction)
    ? state.proposedAction
    : AQUA_ACTION_TEXT.confirm_lot_shuttle;

  const blob = `${observation}\n${interpretation}\n${action}\n${draft.limitations ?? ''}`;
  if (FORBIDDEN.some(pattern => pattern.test(blob))) return null;

  return {
    observation,
    interpretation,
    candidateAction: action,
    confidence: Math.round(draft.confidence * 100) / 100,
    limitations: draft.limitations?.trim()
      || 'Vehicle positions and, when connected, lot occupancy only. No curb, queue, or ADA state.',
    citedEvidenceIds: cited,
  };
}
