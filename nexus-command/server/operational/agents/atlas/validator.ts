import type { DetectionEvidence } from '../../detection/rules.js';
import type { DeskAssessment } from '../desks.js';
import { ATLAS_ALLOWED_CONNECTORS } from '../desks.js';
import { ATLAS_ACTION_TEXT } from './actions.js';
import type { AtlasDraft, AtlasToolState } from './tools.js';

const FORBIDDEN = [
  /change (the |a )?signal/i,
  /retim(e|ing)/i,
  /close (the |a )?road/i,
  /publish .{0,40}(cms|message sign|traveler message)/i,
  /dispatch/i,
  /i (will|am going to) (close|signal|dispatch|publish)/i,
];

const ALLOWED_ACTIONS = new Set(Object.values(ATLAS_ACTION_TEXT));

export function validateAtlasDraft(
  draft: AtlasDraft | null,
  state: AtlasToolState,
  visible: DetectionEvidence[],
): DeskAssessment | null {
  if (!draft) return null;

  const allowedIds = new Set(
    visible
      .filter(item => item.connectorCode !== null
        && (ATLAS_ALLOWED_CONNECTORS as readonly string[]).includes(item.connectorCode))
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
    : ATLAS_ACTION_TEXT.confirm_corridor;

  const blob = `${observation}\n${interpretation}\n${action}\n${draft.limitations ?? ''}`;
  if (FORBIDDEN.some(pattern => pattern.test(blob))) return null;

  return {
    observation,
    interpretation,
    candidateAction: action,
    confidence: Math.round(draft.confidence * 100) / 100,
    limitations: draft.limitations?.trim()
      || 'Segment travel time and probe speed only. Neither identifies the cause of a delay, and ATLAS has no signal-timing or detector data.',
    citedEvidenceIds: cited,
  };
}
