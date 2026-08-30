/**
 * The agent desks.
 *
 * A desk is a domain reviewer, not an oracle. Three rules hold for every one of them, because
 * they are what separate an assessment from a guess:
 *
 *   1. A desk may only read the connectors it declares. It cannot reason about a domain it has
 *      no feed for.
 *   2. A desk must cite the evidence rows behind anything it says.
 *   3. A desk with nothing to say stays silent and reports why. An empty desk is a finding: it
 *      tells the operator which part of the picture Nexus cannot see.
 *
 * Desk missions and boundaries are taken from the agent charter in
 * `nexus-roadmap/nexus-transformation-roadmap.md`.
 */
import type { DetectionEvidence, DetectionMatch } from '../detection/rules.js';

export type FindingStatus = 'contributed' | 'abstained';

export interface DeskConflict {
  /** The desk whose candidate action this one is qualifying. */
  withAgentCode: string;
  concern: string;
  basis: string;
}

export interface DeskAssessment {
  observation: string;
  interpretation: string;
  candidateAction: string;
  confidence: number;
  limitations: string;
  citedEvidenceIds: string[];
  conflicts?: DeskConflict[];
}

export interface DeskContext {
  match: DetectionMatch;
  /** Every observation in the window, before any desk filtering. */
  snapshot: DetectionEvidence[];
  /** Connectors that actually reported in this cycle. */
  liveConnectors: string[];
  now: number;
}

export interface AgentDesk {
  code: string;
  name: string;
  mission: string;
  /** The only connectors this desk is permitted to read. */
  allowedConnectors: string[];
  /** What the desk may never do without an authorized human decision. */
  boundary: string;
  /** Returns null when the desk has evidence but none of it bears on this incident. */
  assess(visible: DetectionEvidence[], context: DeskContext): DeskAssessment | null;
}

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value);
}

function numeric(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function layer(evidence: DetectionEvidence, name: string): boolean {
  return text(evidence.attributes.layer) === name;
}

function ids(evidence: DetectionEvidence[]): string[] {
  return evidence.map(item => item.evidenceId);
}

export const ATLAS_ALLOWED_CONNECTORS = [
  'aldot-algo-traffic-v1',
  'tomtom-traffic-flow-v1',
  'aldot-traffic-counts-v1',
] as const;

export const ATLAS_BOUNDARY = 'ATLAS cannot change a signal plan, close a road, or publish traffic-control instructions.';

export const ATLAS_MISSION = 'Keep arrival and egress corridors moving while avoiding spillback into campus and neighbourhood streets.';

/** Deterministic fallback. The LLM runner calls this when the agent loop fails closed. */
export function assessAtlasRules(visible: DetectionEvidence[]): DeskAssessment | null {
  const events = visible.filter(item => layer(item, 'traffic_event'));
  const congested = visible.filter(item => layer(item, 'travel_time')
    && !['', 'Unaffected', 'Unknown'].includes(text(item.attributes.congestionLevel)));
  const degraded = visible.filter(item => {
    const current = numeric(item.attributes.currentSpeedMph);
    const free = numeric(item.attributes.freeFlowSpeedMph);
    return current !== null && free !== null && free > 0 && current / free < 0.6;
  });
  const cited = [...new Map([...events, ...congested, ...degraded].map(item => [item.evidenceId, item])).values()];
  if (!cited.length) return null;

  const worst = congested[0] ?? degraded[0] ?? events[0];
  const parts: string[] = [];
  if (events.length) parts.push(`${events.length} ALDOT traveler event${events.length === 1 ? '' : 's'} in the Auburn box`);
  if (congested.length) parts.push(`${congested.length} corridor${congested.length === 1 ? '' : 's'} reporting congestion`);
  if (degraded.length) parts.push(`${degraded.length} monitored point${degraded.length === 1 ? '' : 's'} below 60% of free flow`);

  return {
    observation: `${parts.join(', ')}. Closest to this incident: ${worst.summary}`,
    interpretation: congested.length || degraded.length
      ? 'Approach capacity is already reduced, so a change here will land on a corridor with little headroom.'
      : 'State-reported events are present but corridor speed has not yet degraded.',
    candidateAction: 'Confirm the corridor picture with traffic operations before committing to a routing or messaging change.',
    confidence: congested.length || degraded.length ? 0.68 : 0.5,
    limitations: 'Segment travel time and probe speed only. Neither identifies the cause of a delay, and ATLAS has no signal-timing or detector data.',
    citedEvidenceIds: ids(cited).slice(0, 12),
  };
}

const atlas: AgentDesk = {
  code: 'atlas',
  name: 'ATLAS',
  mission: ATLAS_MISSION,
  allowedConnectors: [...ATLAS_ALLOWED_CONNECTORS],
  boundary: ATLAS_BOUNDARY,
  assess(visible) {
    return assessAtlasRules(visible);
  },
};

const forge: AgentDesk = {
  code: 'forge',
  name: 'FORGE',
  mission: 'Identify mobility risks caused by infrastructure conditions.',
  allowedConnectors: ['coa-road-closures-v1', 'usgs-natural-hazards-v1'],
  boundary: 'FORGE cannot operate a pump, utility, traffic cabinet, or any other field device.',
  assess(visible, context) {
    const restrictions = visible.filter(item => text(item.attributes.kind) !== '');
    const rising = visible.filter(item => layer(item, 'stream_gauge') && (numeric(item.attributes.riseFt) ?? 0) > 0);
    const cited = [...restrictions, ...rising];
    if (!cited.length) return null;

    const conflicts: DeskConflict[] = [];
    if (rising.length && context.match.rule.agentCode !== 'forge') {
      conflicts.push({
        withAgentCode: context.match.rule.agentCode,
        concern: 'A routing change may send traffic toward a crossing that is taking on water.',
        basis: `${rising.length} Lee County gauge${rising.length === 1 ? ' is' : 's are'} rising; FORGE cannot tell which crossings are affected without a field inspection.`,
      });
    }

    return {
      observation: `${restrictions.length} City restriction${restrictions.length === 1 ? '' : 's'} published and ${rising.length} stream gauge${rising.length === 1 ? '' : 's'} rising.`,
      interpretation: restrictions.length
        ? 'Published restrictions already constrain the local network that any detour would use.'
        : 'No published restriction competes with this incident right now.',
      candidateAction: 'Verify the published restrictions against field conditions before treating any street as available.',
      confidence: 0.6,
      limitations: 'Published City records and provisional USGS readings only. FORGE observes no barricade placement, standing water, or signal-cabinet health.',
      citedEvidenceIds: ids(cited).slice(0, 12),
      conflicts,
    };
  },
};

export const AQUA_ALLOWED_CONNECTORS = ['auburn-eta-spot-v1', 'auburn-parking-occupancy-v1'] as const;
export const AQUA_BOUNDARY = 'AQUA cannot change an operator schedule or parking policy without agency authorization.';
export const AQUA_MISSION = 'Balance parking, remote-lot, curb, and shuttle demand.';

export function assessAquaRules(visible: DetectionEvidence[], context: DeskContext): DeskAssessment | null {
  const vehicles = visible.filter(item => item.connectorCode === 'auburn-eta-spot-v1');
  if (!vehicles.length) return null;
  const parkingConnected = context.liveConnectors.includes('auburn-parking-occupancy-v1');
  return {
    observation: `${vehicles.length} Tiger Transit observation${vehicles.length === 1 ? '' : 's'} in the current window.`,
    interpretation: parkingConnected
      ? 'Shuttle and lot state can both be weighed against this incident.'
      : 'Shuttle movement is visible but lot occupancy is not, so AQUA cannot tell a full lot from a shuttle problem.',
    candidateAction: 'Ask Parking and Transit to confirm lot and shuttle state before any remote-lot or staging change.',
    confidence: parkingConnected ? 0.6 : 0.35,
    limitations: parkingConnected
      ? 'Vehicle positions and lot occupancy only. No curb, queue, or ADA state.'
      : 'No parking-occupancy feed is connected. AQUA is reasoning from shuttle positions alone.',
    citedEvidenceIds: ids(vehicles).slice(0, 12),
  };
}

const aqua: AgentDesk = {
  code: 'aqua',
  name: 'AQUA',
  mission: AQUA_MISSION,
  allowedConnectors: [...AQUA_ALLOWED_CONNECTORS],
  boundary: AQUA_BOUNDARY,
  assess(visible, context) {
    return assessAquaRules(visible, context);
  },
};

const sentinel: AgentDesk = {
  code: 'sentinel',
  name: 'SENTINEL',
  mission: 'Protect pedestrian movement and public-safety access near campus and the stadium.',
  allowedConnectors: ['nws-weather-alerts-v1', 'auburn-emergency-access-v1'],
  boundary: 'SENTINEL cannot dispatch police, issue a public-safety order, or send a public alert.',
  assess(visible, context) {
    const alerts = visible.filter(item => layer(item, 'weather_alert'));
    const forecast = visible.filter(item => layer(item, 'hourly_forecast'));
    const thunder = forecast.filter(item => (numeric(item.attributes.thunderstormHours) ?? 0) > 0);
    const cited = [...alerts, ...thunder];
    if (!cited.length) return null;

    const conflicts: DeskConflict[] = [];
    if (alerts.length && context.match.rule.agentCode !== 'sentinel') {
      conflicts.push({
        withAgentCode: context.match.rule.agentCode,
        concern: 'Any action that stages people or crews outdoors has to wait on the weather decision.',
        basis: `${alerts.length} National Weather Service product${alerts.length === 1 ? ' is' : 's are'} in force for the operating area.`,
      });
    }

    return {
      observation: alerts.length
        ? `${alerts.length} National Weather Service product in force: ${alerts[0].summary}`
        : `No alert in force. The gridded forecast carries thunderstorm hours in the next window: ${thunder[0].summary}`,
      interpretation: alerts.length
        ? 'Crowd and pedestrian posture is governed by the weather product before anything else.'
        : 'Conditions are forecast to deteriorate, so a decision made now should hold if staging has to move indoors.',
      candidateAction: 'Confirm the crowd and pedestrian posture with the public-safety lead under the severe-weather plan.',
      confidence: alerts.length ? 0.7 : 0.45,
      limitations: 'Weather products and forecast only. SENTINEL has no crowd telemetry, and no emergency-access feed is connected.',
      citedEvidenceIds: ids(cited).slice(0, 12),
      conflicts,
    };
  },
};

const phoenix: AgentDesk = {
  code: 'phoenix',
  name: 'PHOENIX',
  mission: 'Maintain a viable emergency-response corridor.',
  allowedConnectors: ['auburn-emergency-access-v1', 'coa-road-closures-v1'],
  boundary: 'PHOENIX cannot dispatch apparatus, alter clinical decisions, or override incident command.',
  assess(visible, context) {
    const corridorConstrained = context.match.rule.constraints.some(item => /emergency corridor/i.test(item));
    if (!corridorConstrained) return null;

    const accessConnected = context.liveConnectors.includes('auburn-emergency-access-v1');
    const restrictions = visible.filter(item => text(item.attributes.kind) !== '');
    // A contributing desk must cite evidence. Without a published restriction there is nothing
    // PHOENIX is allowed to name, so it stays silent rather than asserting from the constraint alone.
    if (!restrictions.length) return null;

    const conflicts: DeskConflict[] = accessConnected ? [] : [{
      withAgentCode: context.match.rule.agentCode,
      concern: 'The recommended action is constrained to preserve the emergency corridor, but Nexus cannot verify the corridor is clear.',
      basis: 'No emergency-access feed is connected. Corridor state is only available from Event Command.',
    }];

    return {
      observation: accessConnected
        ? `Emergency-access state is reporting, alongside ${restrictions.length} published City restriction${restrictions.length === 1 ? '' : 's'}.`
        : `Corridor state is unobservable. ${restrictions.length} published City restriction${restrictions.length === 1 ? '' : 's'} may bear on it.`,
      interpretation: accessConnected
        ? 'Corridor viability can be weighed directly against this action.'
        : 'This action carries an emergency-corridor constraint that no connected feed can confirm, so a human has to.',
      candidateAction: 'Ask Event Command to confirm the emergency corridor is clear before the action is carried out.',
      confidence: accessConnected ? 0.62 : 0.3,
      limitations: 'No CAD, apparatus-availability, or hospital-status data. Published City restrictions are not a corridor status.',
      citedEvidenceIds: ids(restrictions).slice(0, 12),
      conflicts,
    };
  },
};

const echo: AgentDesk = {
  code: 'echo',
  name: 'ECHO',
  mission: 'Maintain reliable communications and prepare verified public-facing language.',
  allowedConnectors: ['aldot-algo-traffic-v1', 'nexus-siem-alerts-v1'],
  boundary: 'ECHO cannot send a public alert, issue a media statement, or isolate a network.',
  assess(visible, context) {
    const signs = visible.filter(item => layer(item, 'message_sign'));
    const security = visible.filter(item => item.connectorCode === 'nexus-siem-alerts-v1');
    const cited = [...signs, ...security];
    if (!cited.length) return null;

    const displaying = signs.filter(item => {
      const pages = item.attributes.pages;
      return Array.isArray(pages) && pages.some(page => text(page).trim() !== '');
    });

    const conflicts: DeskConflict[] = [];
    if (displaying.length && /messag|advis|inform/i.test(context.match.rule.playbook.recommendedAction)) {
      conflicts.push({
        withAgentCode: context.match.rule.agentCode,
        concern: 'A Nexus-drafted traveler message could contradict what ALDOT is already displaying on this approach.',
        basis: `${displaying.length} ALDOT message sign${displaying.length === 1 ? ' is' : 's are'} already showing traveler text: "${text((displaying[0].attributes.pages as unknown[])[0])}"`,
      });
    }

    return {
      observation: `${signs.length} ALDOT message sign${signs.length === 1 ? '' : 's'} visible, ${displaying.length} currently displaying text.`,
      interpretation: displaying.length
        ? 'The state operator is already communicating on this approach, so any local message has to be consistent with it.'
        : 'No traveler message is currently posted, so drafted language would be the first thing travellers see.',
      candidateAction: 'Draft traveler language for the communications owner to review. ECHO does not send it.',
      confidence: 0.55,
      limitations: 'Message-sign text only. No network health, radio status, or security telemetry is connected.',
      citedEvidenceIds: ids(cited).slice(0, 12),
      conflicts,
    };
  },
};

export const agentDesks: AgentDesk[] = [atlas, sentinel, phoenix, forge, aqua, echo];

/** NEXUS composes; it does not review a domain of its own. */
export const COMPOSER_AGENT_CODE = 'nexus';

export function deskByCode(code: string): AgentDesk | undefined {
  return agentDesks.find(desk => desk.code === code);
}
