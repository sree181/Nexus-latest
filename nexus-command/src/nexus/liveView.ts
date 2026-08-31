import type { GraphSnapshot } from '../graphTypes';
import type {
  AgentFinding,
  Commitment,
  CommitmentState,
  Incident,
  OperationalSnapshot,
  PrincipalContext,
  Recommendation,
  SourceHealth,
  SystemStatus,
} from '../operationalTypes';
import { DESK_ORDER, deskCallsign, serviceLabel, severityLabel } from '../uiCopy';
import type { LiveBundle } from './liveStore';

const HUE: Record<string, string> = {
  atlas: 'oklch(0.70 0.09 250)',
  aqua: 'oklch(0.70 0.09 195)',
  sentinel: 'oklch(0.70 0.09 300)',
  phoenix: '#F0B429',
  forge: 'oklch(0.70 0.09 95)',
  echo: 'oklch(0.70 0.09 150)',
};

const WALL_ROLES: Record<string, string> = {
  atlas: 'Traffic',
  aqua: 'Parking and transit',
  sentinel: 'Public safety',
  phoenix: 'Emergency routes',
  forge: 'Roads and utilities',
  echo: 'Communications',
};

const AVATARS: Record<string, string> = {
  atlas: '/avatars/madeleine-pitts.jpg',
  aqua: '/avatars/maxwell-tan.jpg',
  sentinel: '/avatars/marco-gross.jpg',
  phoenix: '/avatars/fergus-gray.jpg',
  forge: '/avatars/caitlyn-king.jpg',
  echo: '/avatars/courtney-turner.jpg',
};

export const PENDING_STATES = new Set(['draft', 'awaiting_acknowledgement', 'awaiting_approval']);
export const SIGNED_STATES = new Set(['approved']);

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

export function hhmm(iso: string | null | undefined): string {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  const d = new Date(t);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function shortHash(value: string | null | undefined): string {
  if (!value) return '—';
  if (value.length <= 10) return value;
  return `${value.slice(0, 4)}…${value.slice(-3)}`;
}

export function shortId(value: string | null | undefined): string {
  if (!value) return '—';
  return value.replace(/-/g, '').slice(-4);
}

export function minutesLeft(expiresAt: string | null | undefined, now = Date.now()): number | null {
  if (!expiresAt) return null;
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((t - now) / 60_000));
}

export function remainingLabel(minutes: number | null): string {
  if (minutes == null) return '—';
  if (minutes < 1) return 'due now';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

function sentenceStatus(status: string): string {
  const word = status.replace(/_/g, ' ');
  return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
}

function composerLabel(model: string | null | undefined): string {
  const value = (model || '').trim();
  if (!value || /nexus|evidence-bound|composer|policy|detection/i.test(value)) return 'Nexus';
  return value;
}

export function lagLabel(source: SourceHealth): string {
  if (source.connectionStatus === 'permission_required') return 'needs agreement';
  if (source.connectionStatus === 'configuration_required') return 'not set up';
  if (source.connectionStatus === 'not_connected') return 'not connected';
  if (source.connectionStatus === 'disabled') return 'disabled';
  const lag = source.lagSeconds;
  if (lag == null) return source.status === 'healthy' ? 'live' : source.status.replace(/_/g, ' ');
  if (lag < 60) return `${lag}s`;
  if (lag < 3600) return `${Math.round(lag / 60)}m`;
  return `${Math.round(lag / 3600)}h`;
}

export function feedDot(source: SourceHealth): string {
  if (source.connectionStatus && source.connectionStatus !== 'connected') return '#F0B429';
  if (source.status === 'healthy') return '#2FD98A';
  if (source.status === 'delayed' || source.status === 'unverified') return '#F0B429';
  return '#FF4D4F';
}

function firstClause(text: string): string {
  return text.split(/[.!?]/)[0]?.trim() || text.trim();
}

function cardTitle(text: string, max = 160): string {
  const clause = firstClause(text);
  if (clause.length <= max) return clause;
  return `${clause.slice(0, max).replace(/\s+\S*$/, '')}…`;
}

export function findingStance(finding: AgentFinding | undefined): 'contributed' | 'abstained' | 'dissent' | 'none' {
  if (!finding) return 'none';
  if (finding.conflicts.length > 0) return 'dissent';
  return finding.status === 'contributed' ? 'contributed' : 'abstained';
}

export function activeIncident(snapshot: OperationalSnapshot | null): Incident | null {
  if (!snapshot) return null;
  return snapshot.incidents.find(item => ['new', 'triaged', 'active', 'monitoring'].includes(item.status))
    ?? snapshot.incidents[0]
    ?? null;
}

export function activeRecommendation(snapshot: OperationalSnapshot | null, incident: Incident | null): Recommendation | null {
  if (!snapshot) return null;
  if (incident) {
    const match = snapshot.decisionQueue.find(item => item.incidentId === incident.incidentId);
    if (match) return match;
  }
  return snapshot.decisionQueue.find(item => PENDING_STATES.has(item.state))
    ?? snapshot.decisionQueue[0]
    ?? null;
}

export interface DeskRow {
  code: string;
  name: string;
  role: string;
  hue: string;
  line: string;
  note: string;
  statusLabel: string;
  statusAt: string;
  statusColor: string;
  meta: string;
  nameColor: string;
  lineColor: string;
  rowBg: string;
  markFill: string | null;
  markBorder: string | null;
}

function deskRow(code: string, finding: AgentFinding | undefined): DeskRow {
  const stance = findingStance(finding);
  const name = deskCallsign(code);
  const role = WALL_ROLES[code] ?? code;
  const evMeta = finding
    ? `${hhmm(finding.createdAt)} · ev ${shortId(finding.citedEvidenceIds[0])}${finding.citedEvidenceIds.length > 1 ? ` +${finding.citedEvidenceIds.length - 1}` : ''}`
    : '— · not staffed';
  if (stance === 'dissent' && finding) {
    return {
      code, name, role, hue: '#F0B429',
      line: firstClause(finding.conflicts[0]?.concern || finding.interpretation || finding.observation),
      note: firstClause(finding.conflicts[0]?.basis || finding.interpretation),
      statusLabel: 'Dissent', statusAt: `Dissent ${hhmm(finding.createdAt)}`, statusColor: '#F0B429',
      meta: evMeta,
      nameColor: '#F0B429', lineColor: '#F4F2ED', rowBg: 'rgba(240,180,41,0.08)',
      markFill: '#F0B429', markBorder: null,
    };
  }
  if (stance === 'contributed' && finding) {
    return {
      code, name, role, hue: HUE[code] ?? 'rgba(255,255,255,0.16)',
      line: firstClause(finding.observation || finding.interpretation),
      note: firstClause(finding.interpretation || finding.candidateAction),
      statusLabel: 'Contributed', statusAt: `Contributed ${hhmm(finding.createdAt)}`, statusColor: '#2FD98A',
      meta: evMeta,
      nameColor: '#F4F2ED', lineColor: '#F4F2ED', rowBg: '#0B0E13',
      markFill: '#2FD98A', markBorder: null,
    };
  }
  if (stance === 'abstained' && finding) {
    return {
      code, name, role, hue: HUE[code] ?? 'rgba(255,255,255,0.16)',
      line: firstClause(finding.limitations || finding.observation) || 'No permitted feed bears on this incident.',
      note: 'Recorded as abstained, not as agreeing.',
      statusLabel: 'Abstained', statusAt: `Abstained ${hhmm(finding.createdAt)}`, statusColor: '#9AA1AB',
      meta: `${hhmm(finding.createdAt)} · silent`,
      nameColor: '#9AA1AB', lineColor: '#626973', rowBg: '#0B0E13',
      markFill: null, markBorder: '#626973',
    };
  }
  return {
    code, name, role, hue: HUE[code] ?? 'rgba(255,255,255,0.16)',
    line: 'Not staffed on this pack.',
    note: 'Recorded as abstained, not as agreeing.',
    statusLabel: 'Abstained', statusAt: 'Abstained', statusColor: '#9AA1AB',
    meta: '— · not staffed',
    nameColor: '#9AA1AB', lineColor: '#626973', rowBg: '#0B0E13',
    markFill: null, markBorder: '#626973',
  };
}

export interface QueueCard {
  id: string;
  incidentId: string;
  selected: boolean;
  severity: string;
  sevBg: string;
  at: string;
  exp: string;
  title: string;
  place: string;
  tags: string[];
}

export interface EvidenceRow {
  id: string;
  short: string;
  source: string;
  summary: string;
  at: string;
}

export interface FeedChip {
  key: string;
  name: string;
  lag: string;
  dot: string;
  muted: boolean;
}

export interface CommitmentRow {
  id: string;
  agency: string;
  outcome: string;
  owner: string;
  due: string;
  state: string;
  note: string;
  border: string;
  stages: Array<{ key: string; label: string; color: string; weight: string }>;
}

export interface ApprovalParty {
  id: string;
  agency: string;
  role: string;
  status: string;
  statusColor: string;
  fill: string;
}

export interface LinCard {
  id: string;
  kicker: string;
  kickerTone: string;
  title: string;
  meta?: string;
  tone: string;
  bg: string;
}

export interface LinColumn {
  key: string;
  n: number;
  label: string;
  headerTone: string;
  subtitle?: string;
  cards: LinCard[];
}

export interface LinNode {
  stage: string;
  tone: string;
  id: string;
  src: string;
  at: string;
  note: string;
}

export interface LineageView {
  columns: LinColumn[];
  nodes: Record<string, LinNode>;
  edges: Array<[string, string]>;
  weights: Record<string, number>;
}

export interface RecordMark {
  left: string;
  color: string;
  hollow?: boolean;
}

export interface RecordLane {
  code: string;
  name: string;
  hue: string;
  nameColor: string;
  marks: RecordMark[];
}

export interface RecordView {
  ticks: string[];
  detectedPct: string | null;
  recPct: string | null;
  lanes: RecordLane[];
  evidenceMarks: RecordMark[];
}

export interface WallDeskTile {
  code: string;
  name: string;
  role: string;
  avatar: string;
  status: string;
  statusColor: string;
  markFill: string | null;
  markBorder: string | null;
  meta: string;
  gut: string;
  openKey: string;
}

export interface LiveView {
  modeLabel: string;
  modeColor: string;
  eventName: string;
  packLine: string;
  feedLive: number;
  feedTotal: number;
  feedBar: string;
  feedDegraded: number;
  feedOwners: string;
  evidenceCount: number;
  evidenceFrozen: string;
  desksContributed: number;
  desksStaffed: number;
  desksBar: string;
  dissentCount: number;
  abstainedCount: number;
  windowMinutes: string;
  windowUnit: string;
  windowBar: string;
  windowColor: string;
  recStatusLine: string;
  recExpires: string;
  recExpiresRemaining: string;
  snapshotBasis: string;
  dissentNote: string;
  composeLine: string;
  silenceLine: string;
  playbookLine: string;
  detectedLine: string;
  recAuthoredLine: string;
  decidedAt: string;
  approvals: ApprovalParty[];
  commitmentsExecuting: number;
  commitmentsAccepted: number;
  blockedCount: number;
  commitmentsFrom: string;
  sevLabel: string;
  sevBg: string;
  incidentIdLine: string;
  incidentTitle: string;
  incidentOwner: string;
  recVersionLabel: string;
  recMeta: string;
  recAction: string;
  expectedEffect: string;
  limitations: string;
  awaiting: boolean;
  signed: boolean;
  awaitBanner: string;
  awaitClock: string;
  signedBanner: string;
  signedMeta: string;
  deskStrip: string;
  snapshotLine: string;
  hashShort: string;
  recState: string;
  recVersion: string;
  canDecide: boolean;
  operatorName: string;
  operatorRole: string;
  agencyName: string;
  queue: QueueCard[];
  cleared: Array<{ id: string; title: string; meta: string; tone: string }>;
  desks: DeskRow[];
  evidence: EvidenceRow[];
  feeds: FeedChip[];
  commitmentPreview: CommitmentRow[];
  wallDesks: WallDeskTile[];
  lineage: LineageView;
  record: RecordView;
  incidentPoint: [number, number] | null;
  probes: Array<{ name: string; lon: number; lat: number; read: string; sev: number }>;
  noWindow: boolean;
  loading: boolean;
  error: string | null;
  canManageWindow: boolean;
  packs: LiveBundle['packs'];
  recommendation: Recommendation | null;
  incident: Incident | null;
  snapshot: OperationalSnapshot | null;
  graph: GraphSnapshot | null;
  principal: PrincipalContext | null;
}

function commitmentStages(state: CommitmentState): CommitmentRow['stages'] {
  const on = '#2FD98A';
  const off = '#8A929C';
  const hot = '#F4F2ED';
  const bad = '#FF4D4F';
  const blocked = state === 'blocked' || state === 'failed';
  const seen = ['acknowledged', 'approved', 'executing', 'verified', 'blocked', 'failed'].includes(state);
  const accepted = ['approved', 'executing', 'verified'].includes(state);
  const progress = state === 'executing' || state === 'verified';
  const done = state === 'verified';
  const tone = (active: boolean, current: boolean, fail = false) => ({
    color: fail ? bad : current ? hot : active ? on : off,
    weight: current || fail ? '700' : '600',
  });
  return [
    { key: 'asked', label: 'Asked', ...tone(true, state === 'requested') },
    { key: 'seen', label: blocked ? 'Blocked' : 'Seen', ...tone(seen, state === 'acknowledged', blocked && !accepted) },
    { key: 'accepted', label: 'Accepted', ...tone(accepted, state === 'approved') },
    { key: 'progress', label: 'In progress', ...tone(progress, state === 'executing') },
    { key: 'done', label: 'Done', ...tone(done, done) },
  ];
}

function commitmentBorder(state: CommitmentState): string {
  if (state === 'blocked' || state === 'failed') return '#FF4D4F';
  if (state === 'verified' || state === 'executing') return '#2FD98A';
  return 'rgba(255,255,255,0.16)';
}

function ownerLine(incident: Incident | null, principal: PrincipalContext | null): string {
  const owner = incident?.commandOwner;
  if (owner) return `${owner.agencyName} · ${owner.displayName} owns the record`;
  if (principal) return `${principal.agencyName} · ${principal.displayName} is at the desk`;
  return 'No named owner on this incident';
}

function joinNames(names: string[]): string {
  if (names.length === 0) return '';
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(', ')}, and ${names[names.length - 1]}`;
}

export function hhmmss(iso: string | null | undefined): string {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  const d = new Date(t);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}Z`;
}

function linTone(status: string): string {
  if (status === 'Dissent') return '#F0B429';
  if (status === 'Contributed') return '#2FD98A';
  if (status === 'Abstained') return '#8A929C';
  return '#8A929C';
}

export function buildLineage(
  snapshot: OperationalSnapshot | null,
  rec: Recommendation | null,
  incident: Incident | null,
  desks: DeskRow[],
  now = Date.now(),
): LineageView {
  const nodes: Record<string, LinNode> = {};
  const edges: Array<[string, string]> = [];
  const weights: Record<string, number> = {};
  const add = (id: string, node: LinNode) => { nodes[id] = node; };
  const link = (a: string, b: string, w = 1) => {
    edges.push([a, b]);
    weights[`${a}>${b}`] = w;
  };

  const evidence = rec?.evidence ?? [];
  const evCards: LinCard[] = evidence.slice(0, 4).map(item => {
    const id = `ev-${item.evidenceId}`;
    add(id, {
      stage: 'EVIDENCE',
      tone: '#2FD98A',
      id: item.sourceName,
      src: item.sourceName,
      at: hhmmss(item.observedAt),
      note: item.summary,
    });
    return {
      id,
      kicker: shortId(item.evidenceId),
      kickerTone: '#8A929C',
      title: cardTitle(item.summary),
      tone: '#2FD98A',
      bg: '#10141A',
    };
  });
  const uncited = Math.max(0, (snapshot?.observations.length ?? 0) - evidence.length);
  if (uncited > 0) {
    const id = 'ev-uncited';
    add(id, {
      stage: 'EVIDENCE',
      tone: '#8A929C',
      id: `${uncited} UNCITED`,
      src: 'observations in the window',
      at: '—',
      note: `${uncited} observation${uncited === 1 ? '' : 's'} ingested in this window, cited by no finding on this recommendation.`,
    });
    evCards.push({
      id,
      kicker: `+${uncited} RECORDS`,
      kickerTone: '#8A929C',
      title: 'ingested, cited by nothing on this recommendation',
      tone: '#8A929C',
      bg: 'transparent',
    });
  }

  const findingCards: LinCard[] = desks.map(row => {
    const id = `f-${row.code}`;
    add(id, {
      stage: `FINDING · ${row.name}`,
      tone: linTone(row.statusLabel),
      id: row.name,
      src: `${row.name} desk · ${row.statusLabel.toLowerCase()}`,
      at: row.meta.split('·')[0]?.trim() ? `${row.meta.split('·')[0].trim()}Z` : '—',
      note: row.line,
    });
    const finding = rec?.agentFindings.find(item => item.agentCode.toLowerCase() === row.code);
    for (const cited of finding?.citedEvidenceIds ?? []) {
      const evId = `ev-${cited}`;
      if (nodes[evId]) link(evId, id, 1);
    }
    return {
      id,
      kicker: `${row.name} · ${row.statusLabel.toUpperCase()}`,
      kickerTone: linTone(row.statusLabel),
      title: cardTitle(row.line),
      tone: linTone(row.statusLabel),
      bg: row.statusLabel === 'Dissent' ? 'rgba(240,180,41,0.07)' : '#10141A',
    };
  });

  const incCards: LinCard[] = [];
  if (incident) {
    const id = `inc-${incident.incidentId}`;
    add(id, {
      stage: 'INCIDENT',
      tone: '#FF9799',
      id: incident.incidentId.slice(0, 18).toUpperCase(),
      src: incident.commandOwner ? `${incident.commandOwner.agencyName} · ${incident.commandOwner.displayName}` : 'opened from qualifying findings',
      at: hhmmss(incident.detectedAt),
      note: incident.whyItMatters || incident.whatChanged || incident.title,
    });
    for (const row of desks) {
      if (row.statusLabel === 'Contributed' || row.statusLabel === 'Dissent') link(`f-${row.code}`, id, 1);
    }
    incCards.push({
      id,
      kicker: incident.incidentId.slice(0, 18).toUpperCase(),
      kickerTone: '#FF9799',
      title: incident.title,
      meta: `${incident.status.toUpperCase()} · DETECTED ${hhmmss(incident.detectedAt)}`,
      tone: '#FF9799',
      bg: 'rgba(255,77,79,0.07)',
    });
  }

  const recs = (snapshot?.decisionQueue ?? []).filter(item => !incident || item.incidentId === incident.incidentId);
  const recCards: LinCard[] = recs.map(item => {
    const id = `r-${item.recommendationId}`;
    const current = item.recommendationId === rec?.recommendationId;
    const pending = PENDING_STATES.has(item.state);
    const tone = current && pending ? '#F0B429' : SIGNED_STATES.has(item.state) ? '#2FD98A' : '#8A929C';
    add(id, {
      stage: `RECOMMENDATION v${item.version}`,
      tone,
      id: `REC v${item.version}`,
      src: item.generatedBy?.model || 'NEXUS',
      at: hhmmss(item.createdAt),
      note: item.recommendedAction,
    });
    if (incident) link(`inc-${incident.incidentId}`, id, item.evidence.length || 1);
    return {
      id,
      kicker: `REC v${item.version} · ${item.state.replace(/_/g, ' ').toUpperCase()}`,
      kickerTone: tone,
      title: item.recommendedAction,
      meta: `AUTHORED ${hhmmss(item.createdAt)}`,
      tone,
      bg: current && pending ? 'rgba(240,180,41,0.07)' : '#10141A',
    };
  });

  const decCards: LinCard[] = [];
  if (rec && SIGNED_STATES.has(rec.state)) {
    const id = `d-${rec.recommendationId}`;
    add(id, {
      stage: 'DECISION · SIGNED',
      tone: '#2FD98A',
      id: rec.recommendationId.slice(0, 18).toUpperCase(),
      src: 'named decision at the desk',
      at: hhmmss(rec.updatedAt),
      note: rec.recommendedAction,
    });
    link(`r-${rec.recommendationId}`, id, rec.evidence.length || 1);
    decCards.push({
      id,
      kicker: `APPROVED · v${rec.version}`,
      kickerTone: '#2FD98A',
      title: rec.recommendedAction,
      meta: `SIGNED ${hhmmss(rec.updatedAt)}`,
      tone: '#2FD98A',
      bg: 'rgba(47,217,138,0.08)',
    });
  } else if (rec) {
    const id = 'd-gate';
    const left = minutesLeft(rec.expiresAt, now);
    add(id, {
      stage: 'DECISION · NONE',
      tone: '#F0B429',
      id: 'UNSIGNED',
      src: 'requires a named human',
      at: '— not yet',
      note: `Needs expected version v${rec.version}, expected state ${rec.state}, snapshot ${shortHash(rec.evidenceSnapshotHash)}. The wall cannot sign.`,
    });
    link(`r-${rec.recommendationId}`, id, rec.evidence.length || 1);
    decCards.push({
      id,
      kicker: `NO DECISION ON v${rec.version}`,
      kickerTone: '#F0B429',
      title: 'The chain stops here until a named person signs at the desk',
      meta: left == null ? rec.state.replace(/_/g, ' ').toUpperCase() : `AWAITING SIGNATURE · ${left} MIN LEFT`,
      tone: '#F0B429',
      bg: 'rgba(240,180,41,0.10)',
    });
  }

  const commits = incident
    ? (snapshot?.commitments ?? []).filter(item => item.incidentId === incident.incidentId)
    : snapshot?.commitments ?? [];
  const cmtCards: LinCard[] = commits.slice(0, 6).map(item => {
    const id = `c-${item.commitmentId}`;
    const tone = item.state === 'blocked' || item.state === 'failed' ? '#FF9799'
      : item.state === 'verified' || item.state === 'executing' ? '#2FD98A' : '#8A929C';
    add(id, {
      stage: item.state === 'blocked' ? 'COMMITMENT · BLOCKED' : 'COMMITMENT',
      tone,
      id: item.commitmentId.slice(0, 12).toUpperCase(),
      src: item.ownerAgencyName,
      at: hhmm(item.updatedAt) + 'Z',
      note: item.requestedOutcome,
    });
    const signedId = rec && SIGNED_STATES.has(rec.state) ? `d-${rec.recommendationId}` : null;
    if (signedId && nodes[signedId]) link(signedId, id, 1);
    return {
      id,
      kicker: `${item.ownerAgencyName} · ${item.state.replace(/_/g, ' ').toUpperCase()}`,
      kickerTone: tone,
      title: item.requestedOutcome,
      tone,
      bg: item.state === 'blocked' ? 'rgba(255,77,79,0.07)' : '#10141A',
    };
  });
  if (cmtCards.length === 0) {
    const id = 'c-none';
    add(id, {
      stage: 'COMMITMENT',
      tone: '#8A929C',
      id: 'NONE',
      src: '—',
      at: '—',
      note: 'None yet. They appear after a named person signs, not before.',
    });
    cmtCards.push({
      id,
      kicker: 'NONE YET',
      kickerTone: '#8A929C',
      title: 'They appear after a named person signs, not before',
      tone: '#8A929C',
      bg: '#10141A',
    });
  }

  const verified = commits.filter(item => item.state === 'verified');
  const verCards: LinCard[] = verified.slice(0, 4).map(item => {
    const id = `v-${item.commitmentId}`;
    add(id, {
      stage: 'VERIFICATION',
      tone: '#2FD98A',
      id: item.commitmentId.slice(0, 12).toUpperCase(),
      src: item.ownerAgencyName,
      at: hhmmss(item.updatedAt),
      note: item.verificationRule || item.requestedOutcome,
    });
    link(`c-${item.commitmentId}`, id, 1);
    return {
      id,
      kicker: 'VERIFIED',
      kickerTone: '#2FD98A',
      title: item.requestedOutcome,
      tone: '#2FD98A',
      bg: '#10141A',
    };
  });
  if (verCards.length === 0) {
    const id = 'v-none';
    add(id, {
      stage: 'VERIFICATION · NONE',
      tone: '#8A929C',
      id: 'EMPTY',
      src: '—',
      at: '—',
      note: 'Verification requires authoritative evidence IDs, not an operator’s word. Nothing qualifies yet.',
    });
    verCards.push({
      id,
      kicker: 'NOTHING VERIFIED',
      kickerTone: '#8A929C',
      title: 'No commitment has produced authoritative evidence in this window',
      meta: 'CHAIN INCOMPLETE',
      tone: '#8A929C',
      bg: '#10141A',
    });
  }

  const abstained = desks.filter(item => item.statusLabel === 'Abstained').map(item => item.name);
  return {
    nodes,
    edges,
    weights,
    columns: [
      { key: 'evidence', n: 1, label: 'Evidence', headerTone: '#2FD98A', cards: evCards },
      { key: 'finding', n: 2, label: 'Finding', headerTone: '#2FD98A', subtitle: abstained.length ? `${joinNames(abstained)} abstained` : undefined, cards: findingCards },
      { key: 'incident', n: 3, label: 'Incident', headerTone: '#FF4D4F', cards: incCards },
      { key: 'recommendation', n: 4, label: 'Recommendation', headerTone: '#F0B429', cards: recCards },
      { key: 'decision', n: 5, label: 'Decision', headerTone: '#F0B429', cards: decCards },
      { key: 'commitment', n: 6, label: 'Commitment', headerTone: cmtCards[0]?.tone === '#8A929C' ? '#303640' : '#2FD98A', cards: cmtCards },
      { key: 'verification', n: 7, label: 'Verification', headerTone: '#303640', cards: verCards },
    ],
  };
}

export function buildRecord(
  rec: Recommendation | null,
  incident: Incident | null,
  desks: DeskRow[],
  now = Date.now(),
): RecordView {
  const span = 90 * 60_000;
  const start = now - span;
  const ticks: string[] = [];
  for (let i = 0; i < 7; i += 1) ticks.push(hhmm(new Date(start + (span * i) / 6).toISOString()));
  const pos = (iso: string | null | undefined): number | null => {
    if (!iso) return null;
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return null;
    return Math.max(0, Math.min(100, ((t - start) / span) * 100));
  };
  const detected = pos(incident?.detectedAt);
  const recAt = pos(rec?.createdAt);
  const lanes: RecordLane[] = desks.map(row => {
    const finding = rec?.agentFindings.find(item => item.agentCode.toLowerCase() === row.code);
    const left = pos(finding?.createdAt);
    const marks: RecordMark[] = left == null ? [] : [{
      left: `${left}%`,
      color: row.statusLabel === 'Dissent' ? '#F0B429' : row.statusLabel === 'Contributed' ? '#2FD98A' : '#5A6270',
      hollow: row.statusLabel === 'Abstained',
    }];
    return { code: row.code, name: row.name, hue: row.hue, nameColor: row.nameColor, marks };
  });
  const evidenceMarks: RecordMark[] = (rec?.evidence ?? []).flatMap(item => {
    const left = pos(item.observedAt);
    return left == null ? [] : [{ left: `${left}%`, color: '#2FD98A' }];
  });
  return {
    ticks,
    detectedPct: detected == null ? null : `${detected}%`,
    recPct: recAt == null ? null : `${recAt}%`,
    lanes,
    evidenceMarks,
  };
}

function feedOwners(sources: SourceHealth[]): string {
  const tokens = new Set<string>();
  for (const source of sources) {
    const n = source.name.toLowerCase();
    if (n.includes('city') || n.includes('auburn') && n.includes('closure')) tokens.add('coa');
    else if (n.includes('aldot') || n.includes('algo')) tokens.add('aldot');
    else if (n.includes('nws') || n.includes('weather')) tokens.add('nws');
    else if (n.includes('tomtom')) tokens.add('tomtom');
    else if (n.includes('usgs')) tokens.add('usgs');
  }
  return [...tokens].slice(0, 3).join(' · ') || 'feeds';
}

export function buildLiveView(bundle: LiveBundle, selectedIncidentId: string | null = null, now = Date.now()): LiveView {
  const snapshot = bundle.snapshot;
  const principal = bundle.principal;
  const incident = snapshot
    ? (snapshot.incidents.find(item => item.incidentId === selectedIncidentId) ?? activeIncident(snapshot))
    : null;
  const rec = activeRecommendation(snapshot, incident);
  const findings = rec?.agentFindings ?? [];
  const byCode = new Map(findings.map(item => [item.agentCode.toLowerCase(), item]));
  const desks = DESK_ORDER.map(code => deskRow(code, byCode.get(code)));
  const contributed = desks.filter(item => item.statusLabel === 'Contributed').length;
  const dissent = desks.filter(item => item.statusLabel === 'Dissent').length;
  const abstained = desks.filter(item => item.statusLabel === 'Abstained').length;
  const staffed = findings.length || DESK_ORDER.length;
  const sources = snapshot?.sources ?? [];
  const liveFeeds = sources.filter(item => item.status === 'healthy' && (item.connectionStatus ?? 'connected') === 'connected').length;
  const degraded = sources.filter(item => item.status !== 'healthy' || (item.connectionStatus && item.connectionStatus !== 'connected')).length;
  const left = minutesLeft(rec?.expiresAt, now);
  const awaiting = Boolean(rec && PENDING_STATES.has(rec.state));
  const signed = Boolean(rec && SIGNED_STATES.has(rec.state));
  const commitments = snapshot?.commitments ?? [];
  const incidentCommitments = incident
    ? commitments.filter(item => item.incidentId === incident.incidentId)
    : commitments;
  const executing = incidentCommitments.filter(item => item.state === 'executing').length;
  const accepted = incidentCommitments.filter(item => ['acknowledged', 'approved', 'executing', 'verified'].includes(item.state)).length;
  const blocked = incidentCommitments.filter(item => item.state === 'blocked').length;
  const point = incident?.locationGeojson?.type === 'Point' && Array.isArray(incident.locationGeojson.coordinates)
    ? [Number(incident.locationGeojson.coordinates[0]), Number(incident.locationGeojson.coordinates[1])] as [number, number]
    : null;

  const queue = (snapshot?.decisionQueue ?? [])
    .filter(item => PENDING_STATES.has(item.state))
    .map(item => {
      const related = snapshot?.incidents.find(inc => inc.incidentId === item.incidentId) ?? null;
      const sev = related?.severity ?? item.priority;
      return {
        id: item.recommendationId,
        incidentId: item.incidentId,
        selected: item.recommendationId === rec?.recommendationId,
        severity: severityLabel(sev),
        sevBg: sev === 'critical' || sev === 'high' ? '#FF4D4F' : sev === 'medium' ? '#F0B429' : '#2FD98A',
        at: hhmm(related?.detectedAt ?? item.createdAt),
        exp: `exp ${hhmm(item.expiresAt)}`,
        title: firstClause(related?.whyItMatters || item.whyItMatters || item.recommendedAction),
        place: related?.title || item.whatChanged,
        tags: (related?.affectedServices ?? []).slice(0, 3).map(serviceLabel),
      } satisfies QueueCard;
    });

  const cleared = (snapshot?.incidents ?? [])
    .filter(item => ['resolved', 'closed'].includes(item.status))
    .slice(0, 4)
    .map(item => ({
      id: item.incidentId,
      title: item.title,
      meta: `${item.status} ${hhmm(item.resolvedAt ?? item.updatedAt)}`,
      tone: item.status === 'resolved' ? '#2FD98A' : '#626973',
    }));

  const probes = (snapshot?.observations ?? [])
    .filter(item => item.geometryGeojson?.type === 'Point' && Array.isArray(item.geometryGeojson.coordinates))
    .slice(0, 6)
    .map(item => {
      const coords = item.geometryGeojson!.coordinates as number[];
      return {
        name: item.sourceName,
        lon: Number(coords[0]),
        lat: Number(coords[1]),
        read: firstClause(item.summary).slice(0, 42),
        sev: item.qualityFlags.includes('stale') ? 0.5 : 0.2,
      };
    });

  return {
    modeLabel: bundle.status?.mode === 'live' ? 'live' : (bundle.status?.mode || 'review'),
    modeColor: bundle.status?.mode === 'live' ? '#2FD98A' : '#F0B429',
    eventName: snapshot?.event.name ?? 'Auburn Mobility Operations',
    packLine: snapshot?.event.scenarioPackCode ? `pack ${snapshot.event.scenarioPackCode}` : 'no pack',
    feedLive: liveFeeds,
    feedTotal: sources.length,
    feedBar: sources.length ? `${Math.round((liveFeeds / sources.length) * 100)}%` : '0%',
    feedDegraded: degraded,
    feedOwners: feedOwners(sources),
    evidenceCount: rec?.evidence.length || snapshot?.observations.length || 0,
    evidenceFrozen: rec ? `frozen ${hhmm(rec.createdAt)}` : 'no snapshot',
    desksContributed: contributed,
    desksStaffed: staffed,
    desksBar: staffed ? `${Math.round((contributed / staffed) * 100)}%` : '0%',
    dissentCount: dissent,
    abstainedCount: abstained,
    windowMinutes: left == null ? '—' : String(left),
    windowUnit: left == null ? 'no expiry' : 'min left',
    windowBar: left == null ? '0%' : `${Math.min(100, Math.round((left / 90) * 100))}%`,
    windowColor: awaiting ? '#F0B429' : signed ? '#2FD98A' : '#8A929C',
    recStatusLine: rec ? `rec v${rec.version} ${rec.state.replace(/_/g, ' ')}` : 'no recommendation',
    recExpires: rec ? `expires ${hhmm(rec.expiresAt)}` : '—',
    recExpiresRemaining: rec
      ? `expires ${hhmm(rec.expiresAt)} · ${left == null ? '—' : `${left} min remaining`}`
      : 'no expiry',
    snapshotBasis: rec
      ? `${shortHash(rec.evidenceSnapshotHash)} · frozen ${hhmm(rec.createdAt)}Z · ${rec.evidence.length || snapshot?.observations.length || 0} evidence rows`
      : 'No frozen snapshot',
    dissentNote: dissent
      ? `${desks.find(item => item.statusLabel === 'Dissent')?.name}: ${desks.find(item => item.statusLabel === 'Dissent')?.line}. Carried to the approver unresolved — NEXUS does not overrule a desk.`
      : 'No dissent is on this snapshot.',
    composeLine: `One recommendation composed from ${contributed + dissent} finding${contributed + dissent === 1 ? '' : 's'}.`,
    silenceLine: abstained
      ? `${joinNames(desks.filter(item => item.statusLabel === 'Abstained').map(item => item.name))} ${abstained === 1 ? 'is' : 'are'} recorded as abstained, not as agreeing.`
      : 'No desk is silent on this snapshot.',
    playbookLine: snapshot?.event.scenarioPackCode ?? 'no pack',
    detectedLine: incident ? `detected ${hhmm(incident.detectedAt)}` : 'no incident',
    recAuthoredLine: rec ? `recommendation v${rec.version} ${hhmm(rec.createdAt)}` : 'no recommendation',
    decidedAt: signed && rec ? hhmm(rec.updatedAt) : '— not yet',
    approvals: (rec?.approvalRequirements.length
      ? rec.approvalRequirements.map(req => ({
          id: req.requirementId,
          agency: req.agencyName,
          role: `${req.status === 'pending' ? 'Required' : req.status} · ${req.roleCode.replace(/_/g, ' ')}`,
          status: req.status === 'satisfied' ? 'Satisfied' : req.status === 'waived' ? 'Waived' : 'Pending',
          statusColor: req.status === 'satisfied' || req.status === 'waived' ? '#2FD98A' : '#F0B429',
          fill: req.status === 'satisfied' || req.status === 'waived' ? '#2FD98A' : '#F0B429',
        }))
      : principal
        ? [{
            id: principal.principalId,
            agency: principal.agencyName,
            role: 'Required · named decision',
            status: signed ? 'Satisfied' : awaiting ? 'Pending' : '—',
            statusColor: signed ? '#2FD98A' : '#F0B429',
            fill: signed ? '#2FD98A' : '#F0B429',
          }]
        : []),
    commitmentsExecuting: executing,
    commitmentsAccepted: accepted || incidentCommitments.length,
    blockedCount: blocked,
    commitmentsFrom: incidentCommitments[0] ? `from ${hhmm(incidentCommitments[0].updatedAt)}` : 'none yet',
    sevLabel: incident ? severityLabel(incident.severity).toUpperCase() : '—',
    sevBg: incident && (incident.severity === 'high' || incident.severity === 'critical') ? '#FF4D4F' : '#F0B429',
    incidentIdLine: incident
      ? `${sentenceStatus(incident.status)} · detected ${hhmm(incident.detectedAt)}Z`
      : 'No open incident',
    incidentTitle: incident?.title ?? 'No incident requires a decision',
    incidentOwner: ownerLine(incident, principal),
    recVersionLabel: rec ? `Recommendation v${rec.version}` : 'No recommendation',
    recMeta: rec
      ? `Composed by ${composerLabel(rec.generatedBy?.model)} · expires ${hhmm(rec.expiresAt)}Z`
      : 'The playbook has not composed a next step.',
    recAction: rec?.recommendedAction ?? 'Nothing to sign. The desks have not produced a recommendation.',
    expectedEffect: rec?.expectedEffect ?? '—',
    limitations: rec?.limitations ?? '—',
    awaiting,
    signed,
    awaitBanner: 'Awaiting signature',
    awaitClock: `${principal?.displayName || 'Unsigned'} · ${remainingLabel(left)}`,
    signedBanner: rec ? `Approved · v${rec.version}` : 'Signed',
    signedMeta: `${incidentCommitments.length} commitment${incidentCommitments.length === 1 ? '' : 's'}`,
    deskStrip: `${staffed} staffed · ${contributed} contributed · ${abstained} abstained · ${dissent} dissent`,
    snapshotLine: rec
      ? `SNAPSHOT ${shortHash(rec.evidenceSnapshotHash)} · ${hhmm(rec.createdAt)} · IMMUTABLE`
      : 'NO FROZEN SNAPSHOT',
    hashShort: shortHash(rec?.evidenceSnapshotHash),
    recState: rec?.state.replace(/_/g, ' ') ?? '—',
    recVersion: rec ? `v${rec.version}` : '—',
    canDecide: Boolean(rec && awaiting),
    operatorName: principal?.displayName ?? 'Unsigned',
    operatorRole: principal ? `${principal.roles[0]?.replace(/_/g, ' ') || 'operator'} · ${principal.agencyName}` : 'Sign in required for a write',
    agencyName: principal?.agencyName ?? '—',
    queue,
    cleared,
    desks,
    evidence: (rec?.evidence ?? []).slice(0, 8).map(item => ({
      id: item.evidenceId,
      short: shortId(item.evidenceId),
      source: item.sourceName,
      summary: item.summary,
      at: hhmm(item.observedAt),
    })),
    feeds: sources.slice(0, 10).map(item => ({
      key: item.sourceId,
      name: item.name,
      lag: lagLabel(item),
      dot: feedDot(item),
      muted: (item.connectionStatus ?? 'connected') !== 'connected' || item.status !== 'healthy',
    })),
    commitmentPreview: incidentCommitments.slice(0, 6).map((item: Commitment) => ({
      id: item.commitmentId,
      agency: item.ownerAgencyName,
      outcome: item.requestedOutcome,
      owner: item.assignee?.displayName ?? 'unassigned',
      due: item.dueAt ? `due ${hhmm(item.dueAt)}` : item.state.replace(/_/g, ' '),
      state: item.state,
      note: item.blocker || item.verificationRule || item.state.replace(/_/g, ' '),
      border: commitmentBorder(item.state),
      stages: commitmentStages(item.state),
    })),
    wallDesks: DESK_ORDER.map(code => {
      const row = deskRow(code, byCode.get(code));
      const finding = byCode.get(code);
      const status = row.statusLabel === 'Dissent' ? 'DISSENT' : row.statusLabel === 'Contributed' ? 'CONTRIB' : 'ABSTAIN';
      return {
        code,
        name: deskCallsign(code),
        role: WALL_ROLES[code] ?? code,
        avatar: AVATARS[code],
        status,
        statusColor: row.statusColor,
        markFill: row.markFill,
        markBorder: row.markBorder,
        meta: finding?.modelVersion
          ? `${finding.modelVersion} · EV ${finding.citedEvidenceIds.length} · CONF ${finding.confidence ?? '—'}`
          : `rule · EV ${finding?.citedEvidenceIds.length ?? '—'} · CONF ${finding?.confidence ?? '—'}`,
        gut: row.hue,
        openKey: `open_${code}`,
      };
    }),
    lineage: buildLineage(snapshot, rec, incident, desks, now),
    record: buildRecord(rec, incident, desks, now),
    incidentPoint: point,
    probes,
    noWindow: bundle.noWindow,
    loading: bundle.loading,
    error: bundle.error,
    canManageWindow: Boolean(principal?.scopes.includes('event:manage')),
    packs: bundle.packs,
    recommendation: rec,
    incident,
    snapshot,
    graph: bundle.graph,
    principal,
  };
}
