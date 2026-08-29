/**
 * Runs every desk the active scenario pack staffs against one immutable evidence snapshot, then
 * has NEXUS compose the result.
 *
 * The composition never invents an action. The authored playbook still supplies what to do; what
 * NEXUS adds is who looked, who could not, and where the desks disagree. A recommendation that
 * three desks support is a different thing from one that three desks could not evaluate, and the
 * operator is entitled to see which one is in front of them.
 */
import { authoritativeConnectors } from '../../connectors/registry.js';
import type { DetectionEvidence, DetectionMatch } from '../detection/rules.js';
import {
  agentDesks,
  COMPOSER_AGENT_CODE,
  type AgentDesk,
  type DeskConflict,
  type DeskContext,
  type FindingStatus,
} from './desks.js';

export interface DeskFinding {
  agentCode: string;
  agentName: string;
  status: FindingStatus;
  observation: string;
  interpretation: string;
  candidateAction: string;
  confidence: number | null;
  limitations: string;
  citedEvidenceIds: string[];
  conflicts: DeskConflict[];
}

export interface DeskComposition {
  findings: DeskFinding[];
  contributors: string[];
  /** Desks that were staffed but had nothing to say, and why. */
  silent: Array<{ agentCode: string; agentName: string; reason: string }>;
  conflicts: Array<DeskConflict & { fromAgentCode: string }>;
}

function connectorNameMap(): Map<string, string> {
  return new Map(authoritativeConnectors.map(connector => [connector.definition.code, connector.definition.name]));
}

function describe(codes: string[], names: Map<string, string>): string {
  const labels = codes.map(code => names.get(code) ?? code);
  if (labels.length === 1) return labels[0];
  return `${labels.slice(0, -1).join(', ')} or ${labels[labels.length - 1]}`;
}

export interface ComposeOptions {
  /** Desk codes the active pack staffs. NEXUS is filtered out; it composes rather than reviews. */
  staffedAgentCodes: string[];
  match: DetectionMatch;
  snapshot: DetectionEvidence[];
  liveConnectors: string[];
  desks?: AgentDesk[];
  connectorNames?: Map<string, string>;
  now?: number;
}

export function composeDeskFindings(options: ComposeOptions): DeskComposition {
  const names = options.connectorNames ?? connectorNameMap();
  const roster = options.desks ?? agentDesks;
  const staffed = roster.filter(desk => options.staffedAgentCodes.includes(desk.code));
  const context: DeskContext = {
    match: options.match,
    snapshot: options.snapshot,
    liveConnectors: options.liveConnectors,
    now: options.now ?? Date.now(),
  };

  const findings: DeskFinding[] = [];
  const silent: DeskComposition['silent'] = [];
  const conflicts: DeskComposition['conflicts'] = [];

  for (const desk of staffed) {
    const visible = options.snapshot.filter(item => item.connectorCode !== null
      && desk.allowedConnectors.includes(item.connectorCode));

    const connected = desk.allowedConnectors.filter(code => options.liveConnectors.includes(code));
    let reason: string | null = null;
    let assessment = null;

    if (!connected.length) {
      reason = `No ${describe(desk.allowedConnectors, names)} feed is connected, so ${desk.name} cannot evaluate this incident.`;
    } else {
      assessment = desk.assess(visible, context);
      if (!assessment) {
        reason = `${desk.name} reviewed ${visible.length} observation${visible.length === 1 ? '' : 's'} from ${describe(connected, names)} and found nothing that bears on this incident.`;
      }
    }

    if (!assessment) {
      const abstention = reason ?? `${desk.name} had nothing to report.`;
      silent.push({ agentCode: desk.code, agentName: desk.name, reason: abstention });
      findings.push({
        agentCode: desk.code,
        agentName: desk.name,
        status: 'abstained',
        observation: abstention,
        interpretation: `${desk.mission} Nexus holds no evidence that lets ${desk.name} speak to this incident.`,
        candidateAction: 'None. This desk is silent by design rather than in agreement.',
        confidence: null,
        limitations: desk.boundary,
        citedEvidenceIds: [],
        conflicts: [],
      });
      continue;
    }

    const deskConflicts = assessment.conflicts ?? [];
    for (const conflict of deskConflicts) {
      conflicts.push({ ...conflict, fromAgentCode: desk.code });
    }
    findings.push({
      agentCode: desk.code,
      agentName: desk.name,
      status: 'contributed',
      observation: assessment.observation,
      interpretation: assessment.interpretation,
      candidateAction: assessment.candidateAction,
      confidence: assessment.confidence,
      limitations: `${assessment.limitations} ${desk.boundary}`.trim(),
      citedEvidenceIds: assessment.citedEvidenceIds,
      conflicts: deskConflicts,
    });
  }

  const contributors = findings.filter(finding => finding.status === 'contributed').map(finding => finding.agentCode);
  findings.push(composerFinding(options.match, findings, contributors, silent, conflicts));

  return { findings, contributors, silent, conflicts };
}

function nameOf(findings: DeskFinding[], code: string): string {
  return findings.find(finding => finding.agentCode === code)?.agentName ?? code.toUpperCase();
}

function composerFinding(
  match: DetectionMatch,
  findings: DeskFinding[],
  contributors: string[],
  silent: DeskComposition['silent'],
  conflicts: DeskComposition['conflicts'],
): DeskFinding {
  const contributorNames = contributors.map(code => nameOf(findings, code));
  const silentNames = silent.map(entry => entry.agentName);

  const observation = contributorNames.length
    ? `${contributorNames.join(', ')} contributed. ${silentNames.length ? `${silentNames.join(', ')} had no evidence to offer.` : 'Every staffed desk reported.'}`
    : `No staffed desk could evaluate this incident. ${silentNames.join(', ')} all reported no usable evidence.`;

  const interpretation = conflicts.length
    ? `The desks do not fully agree. ${conflicts.map(conflict => `${nameOf(findings, conflict.fromAgentCode)}: ${conflict.concern}`).join(' ')}`
    : 'No desk raised a conflict with the recommended action.';

  return {
    agentCode: COMPOSER_AGENT_CODE,
    agentName: 'NEXUS',
    status: contributorNames.length ? 'contributed' : 'abstained',
    observation,
    interpretation,
    // NEXUS reconciles and sequences. It does not author a different action than the approved playbook.
    candidateAction: match.rule.playbook.recommendedAction,
    confidence: null,
    limitations: silentNames.length
      ? `This recommendation was composed without ${silentNames.join(', ')}. Treat their domains as unassessed, not as clear.`
      : 'Composed from the staffed desks listed above and the cited evidence only.',
    citedEvidenceIds: [...new Set(findings.flatMap(finding => finding.citedEvidenceIds))].slice(0, 24),
    conflicts: conflicts.map(({ fromAgentCode, ...rest }) => ({ ...rest, withAgentCode: rest.withAgentCode || fromAgentCode })),
  };
}
