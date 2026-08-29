import { createHash } from 'node:crypto';

/**
 * Detection turns authoritative observations into named reasons an incident may open.
 *
 * The operational metadata for a rule (severity, playbook, owning agencies) lives in the
 * `detection_rules` table so it can be tuned per scenario pack. The predicate that decides
 * whether an upstream record actually qualifies lives here, in versioned code, because a
 * wrong predicate invents an incident and that is the one failure the platform must not have.
 */

export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'informational';

export interface DetectionEvidence {
  evidenceId: string;
  connectorCode: string | null;
  sourceEventId: string;
  sourceName: string;
  summary: string;
  observedAt: string;
  /** Upstream content digest. Evidence rows are updated in place, so identity alone is not enough. */
  contentHash: string;
  geometryGeojson: unknown;
  attributes: Record<string, unknown>;
}

export interface PlaybookCommitment {
  agencyCode: string;
  requestedOutcome: string;
  verificationRule: string;
  dueInMinutes: number;
}

export interface PlaybookApproval {
  agencyCode: string;
  roleCode: string;
}

export interface Playbook {
  recommendedAction: string;
  expectedEffect: string;
  limitations: string;
  approvals: PlaybookApproval[];
  commitments: PlaybookCommitment[];
}

export interface DetectionRuleDefinition {
  packCode: string;
  ruleCode: string;
  connectorCode: string;
  agentCode: string;
  name: string;
  whyItMatters: string;
  severity: Severity;
  affectedServices: string[];
  constraints: string[];
  playbook: Playbook;
}

export interface RuleHit {
  /** Stable identity of the upstream thing, not of the observation that reported it. */
  externalKey: string;
  title: string;
  whatChanged: string;
  severityOverride?: Severity;
}

export interface DetectionMatch {
  rule: DetectionRuleDefinition;
  externalKey: string;
  title: string;
  whatChanged: string;
  severity: Severity;
  primary: DetectionEvidence;
  evidence: DetectionEvidence[];
  evidenceHash: string;
}

type Predicate = (evidence: DetectionEvidence, now: number) => RuleHit | null;

/** A monitored point below this share of free-flow speed is worth a human look. */
export const FLOW_REVIEW_RATIO = 0.6;
/** Probe speed below this confidence is not trustworthy enough to open an incident. */
export const FLOW_MIN_CONFIDENCE = 0.7;

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value);
}

function numeric(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function epochOf(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const asNumber = numeric(value);
  const candidate = asNumber !== null ? new Date(asNumber) : new Date(String(value));
  const time = candidate.getTime();
  return Number.isNaN(time) ? null : time;
}

/** A published restriction counts only while it is actually in force. */
export function isInEffect(startValue: unknown, endValue: unknown, now: number): boolean {
  const start = epochOf(startValue);
  const end = epochOf(endValue);
  if (start === null && end === null) return true;
  if (start !== null && start > now) return false;
  if (end !== null && end < now) return false;
  return true;
}

function algoEvent(expectedType: string): Predicate {
  return evidence => {
    const attributes = evidence.attributes;
    if (text(attributes.layer) !== 'traffic_event') return null;
    if (text(attributes.eventType) !== expectedType) return null;
    const where = text(attributes.subtitle) || text(attributes.route);
    return {
      externalKey: `algo-event:${text(attributes.algoEventId)}`,
      title: `${expectedType} reported on ${text(attributes.route) || 'the Auburn approach'}`,
      whatChanged: `ALDOT published ${expectedType.toLowerCase()} "${text(attributes.title)}"${where ? ` at ${where}` : ''}.${
        text(attributes.description) ? ` ${text(attributes.description)}` : ''
      }`.replace(/\s+/g, ' ').trim(),
    };
  };
}

const predicates: Record<string, Predicate> = {
  'algo-crash': algoEvent('Crash'),
  'algo-incident': algoEvent('Incident'),
  'algo-roadwork': algoEvent('Roadwork'),

  'algo-corridor-congestion': evidence => {
    const attributes = evidence.attributes;
    if (text(attributes.layer) !== 'travel_time') return null;
    const level = text(attributes.congestionLevel);
    if (!level || level === 'Unaffected' || level === 'Unknown') return null;
    const name = text(attributes.name) || 'the Auburn corridor';
    return {
      externalKey: `algo-corridor:${text(attributes.algoTravelTimeId)}`,
      title: `${level} congestion on ${name}`,
      whatChanged: `ALDOT travel time for ${name} reports ${level.toLowerCase()} congestion at ${
        text(attributes.averageSpeedMph) || 'an unreported'
      } mph, ${text(attributes.estimatedTimeMinutes) || 'unknown'} minutes end to end.`,
      severityOverride: level === 'Heavy' || level === 'Severe' ? 'high' : undefined,
    };
  },

  'city-restriction-in-effect': (evidence, now) => {
    const attributes = evidence.attributes;
    const kind = text(attributes.kind);
    if (!kind) return null;
    if (!isInEffect(attributes.startsAt, attributes.endsAt, now)) return null;
    const road = text(attributes.road) || 'an unnamed road';
    return {
      externalKey: `city-${kind}:${evidence.sourceEventId}`,
      title: `City ${kind} in effect on ${road}`,
      whatChanged: `City of Auburn publishes an in-effect ${kind} on ${road}: ${text(attributes.description)}`.trim(),
    };
  },

  'tomtom-corridor-degraded': evidence => {
    const attributes = evidence.attributes;
    const pointCode = text(attributes.pointCode);
    if (!pointCode) return null;
    const confidence = numeric(attributes.confidence);
    if (confidence !== null && confidence < FLOW_MIN_CONFIDENCE) return null;
    const current = numeric(attributes.currentSpeedMph);
    const freeFlow = numeric(attributes.freeFlowSpeedMph);
    const closed = attributes.roadClosure === true;
    if (!closed && (current === null || freeFlow === null || freeFlow <= 0 || current / freeFlow >= FLOW_REVIEW_RATIO)) return null;
    const name = text(attributes.pointName) || pointCode;
    const share = current !== null && freeFlow ? Math.round((current / freeFlow) * 100) : null;
    return {
      externalKey: `tomtom-point:${pointCode}`,
      title: closed ? `Road closure reported at ${name}` : `${name} running at ${share}% of free flow`,
      whatChanged: closed
        ? `Licensed road-flow data reports a closure at ${name}.`
        : `Licensed road-flow data reports ${current} mph at ${name} against a ${freeFlow} mph free-flow baseline (${share}%).`,
      severityOverride: closed ? 'high' : undefined,
    };
  },

  'nws-alert-active': (evidence, now) => {
    const attributes = evidence.attributes;
    const alertId = text(attributes.alertId);
    if (!alertId) return null;
    const expires = epochOf(attributes.expiresAt);
    if (expires !== null && expires < now) return null;
    const eventName = text(attributes.eventName) || 'Weather alert';
    return {
      externalKey: `nws-alert:${alertId}`,
      title: `${eventName} in effect for the operating area`,
      whatChanged: `National Weather Service issued ${eventName}${
        text(attributes.headline) ? `: ${text(attributes.headline)}` : ''
      }`,
      severityOverride: text(attributes.severity) === 'Extreme' ? 'critical' : undefined,
    };
  },

  'siem-critical-alert': evidence => {
    const attributes = evidence.attributes;
    const alertId = text(attributes.alertId);
    if (!alertId) return null;
    const severity = text(attributes.severity).toLowerCase();
    if (severity !== 'critical' && severity !== 'high') return null;
    return {
      externalKey: `siem-alert:${alertId}`,
      title: `${text(attributes.title) || 'Security alert'} affecting operational systems`,
      whatChanged: `Security monitoring raised a ${severity} alert on ${text(attributes.assetName) || 'an operational system'}.`,
      severityOverride: severity === 'critical' ? 'critical' : undefined,
    };
  },
};

export function hasPredicate(ruleCode: string): boolean {
  return Object.prototype.hasOwnProperty.call(predicates, ruleCode);
}

/**
 * Identifies both which evidence was cited and what it said. A connector updates an evidence row
 * in place when upstream content changes, so hashing identity alone would let a recommendation
 * keep a stale approval against changed facts.
 */
export function evidenceHash(evidence: Array<Pick<DetectionEvidence, 'evidenceId' | 'contentHash'>>): string {
  const parts = evidence.map(item => `${item.evidenceId}:${item.contentHash}`).sort();
  return createHash('sha256').update(parts.join('|')).digest('hex');
}

/**
 * Evaluates every active rule against the current evidence window and returns one match per
 * distinct upstream record. Observations that report the same upstream thing collapse into a
 * single match so the same crash never opens two incidents.
 */
export function evaluateRules(
  rules: DetectionRuleDefinition[],
  evidence: DetectionEvidence[],
  now = Date.now(),
): DetectionMatch[] {
  const grouped = new Map<string, DetectionMatch>();

  for (const rule of rules) {
    const predicate = predicates[rule.ruleCode];
    if (!predicate) continue;

    for (const observation of evidence) {
      if (observation.connectorCode !== rule.connectorCode) continue;
      const hit = predicate(observation, now);
      if (!hit) continue;

      const key = `${rule.ruleCode}|${hit.externalKey}`;
      const existing = grouped.get(key);
      if (!existing) {
        grouped.set(key, {
          rule,
          externalKey: hit.externalKey,
          title: hit.title,
          whatChanged: hit.whatChanged,
          severity: hit.severityOverride ?? rule.severity,
          primary: observation,
          evidence: [observation],
          evidenceHash: '',
        });
        continue;
      }

      existing.evidence.push(observation);
      if (Date.parse(observation.observedAt) > Date.parse(existing.primary.observedAt)) {
        existing.primary = observation;
        existing.title = hit.title;
        existing.whatChanged = hit.whatChanged;
        existing.severity = hit.severityOverride ?? rule.severity;
      }
    }
  }

  const matches = [...grouped.values()];
  for (const match of matches) {
    match.evidence.sort((left, right) => Date.parse(right.observedAt) - Date.parse(left.observedAt));
    match.evidence = match.evidence.slice(0, 12);
    match.evidenceHash = evidenceHash(match.evidence);
  }

  const rank: Record<Severity, number> = { critical: 0, high: 1, medium: 2, low: 3, informational: 4 };
  return matches.sort((left, right) => rank[left.severity] - rank[right.severity]
    || Date.parse(right.primary.observedAt) - Date.parse(left.primary.observedAt));
}

export function representativePoint(geometry: unknown): Record<string, unknown> | null {
  if (!geometry || typeof geometry !== 'object') return null;
  const value = geometry as { type?: string; coordinates?: unknown };
  const coordinates = value.coordinates;
  if (value.type === 'Point' && Array.isArray(coordinates) && coordinates.length >= 2) {
    return { type: 'Point', coordinates: [Number(coordinates[0]), Number(coordinates[1])] };
  }
  if (value.type === 'LineString' && Array.isArray(coordinates) && Array.isArray(coordinates[0])) {
    const midpoint = coordinates[Math.floor(coordinates.length / 2)] as unknown[];
    if (Array.isArray(midpoint) && midpoint.length >= 2) {
      return { type: 'Point', coordinates: [Number(midpoint[0]), Number(midpoint[1])] };
    }
  }
  if (value.type === 'MultiLineString' && Array.isArray(coordinates) && Array.isArray(coordinates[0]) && Array.isArray((coordinates[0] as unknown[])[0])) {
    const first = (coordinates[0] as unknown[])[0] as unknown[];
    return { type: 'Point', coordinates: [Number(first[0]), Number(first[1])] };
  }
  if (value.type === 'Polygon' && Array.isArray(coordinates) && Array.isArray(coordinates[0]) && Array.isArray((coordinates[0] as unknown[])[0])) {
    const first = (coordinates[0] as unknown[])[0] as unknown[];
    return { type: 'Point', coordinates: [Number(first[0]), Number(first[1])] };
  }
  return null;
}
