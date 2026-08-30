/** Operator-facing words. Keep jargon in the data model; do not put it on the glass. */

const DESK_NAMES: Record<string, string> = {
  atlas: 'Traffic',
  aqua: 'Parking & transit',
  sentinel: 'Public safety',
  phoenix: 'Emergency routes',
  forge: 'Roads & utilities',
  echo: 'Communications',
  nexus: 'Coordinator',
};

const DESK_CALLSIGNS: Record<string, string> = {
  atlas: 'ATLAS',
  aqua: 'AQUA',
  sentinel: 'SENTINEL',
  phoenix: 'PHOENIX',
  forge: 'FORGE',
  echo: 'ECHO',
  nexus: 'NEXUS',
};

export const DESK_ORDER = ['atlas', 'aqua', 'sentinel', 'phoenix', 'forge', 'echo'] as const;

export const DESK_ICONS: Record<string, string> = {
  atlas: '/icons/desks/atlas.svg',
  aqua: '/icons/desks/aqua.svg',
  sentinel: '/icons/desks/sentinel.svg',
  phoenix: '/icons/desks/phoenix.svg',
  forge: '/icons/desks/forge.svg',
  echo: '/icons/desks/echo.svg',
};

const CONNECTION: Record<string, string> = {
  connected: 'Connected',
  permission_required: 'Needs a partner agreement',
  configuration_required: 'Not set up yet',
  not_connected: 'Not connected',
};

const PHASE: Record<string, string> = {
  readiness: 'Getting ready',
  arrival: 'Arrival',
  ingress: 'Coming in',
  in_game: 'During the event',
  egress: 'Leaving',
  after_action: 'After-action',
  closed: 'Closed',
  steady_state: 'Everyday operations',
  response: 'Response',
  recovery: 'Recovery',
};

const SERVICE: Record<string, string> = {
  traffic: 'Roads',
  ingress: 'Arrival routes',
  egress: 'Departure routes',
  parking: 'Parking',
  transit: 'Transit',
  emergency_access: 'Emergency access',
  public_safety: 'Public safety',
  public_works: 'Public works',
  communications: 'Communications',
  operational_technology: 'Systems',
};

const CLASSIFICATION: Record<string, string> = {
  live: 'Live',
  reference: 'Background',
  restricted: 'Restricted',
};

const COMMITMENT: Record<string, string> = {
  requested: 'Asked',
  acknowledged: 'Seen',
  approved: 'Accepted',
  executing: 'In progress',
  blocked: 'Blocked',
  verified: 'Done',
  failed: 'Failed',
  expired: 'Expired',
  cancelled: 'Cancelled',
};

export function deskName(code: string): string {
  return DESK_NAMES[code.toLowerCase()] ?? code.replace(/_/g, ' ');
}

export function deskCallsign(code: string): string {
  return DESK_CALLSIGNS[code.toLowerCase()] ?? code.toUpperCase();
}

export function connectionLabel(status: string | null | undefined): string {
  if (!status) return 'Unknown';
  return CONNECTION[status] ?? status.replace(/_/g, ' ');
}

export function phaseLabel(phase: string): string {
  return PHASE[phase] ?? phase.replace(/_/g, ' ');
}

export function serviceLabel(service: string): string {
  return SERVICE[service] ?? service.replace(/_/g, ' ');
}

export function classificationLabel(value: string | null | undefined): string {
  if (!value) return 'Live';
  return CLASSIFICATION[value] ?? value.replace(/_/g, ' ');
}

export function commitmentLabel(state: string): string {
  return COMMITMENT[state] ?? state.replace(/_/g, ' ');
}

export function severityLabel(severity: string): string {
  const word = severity.replace(/_/g, ' ');
  return word.charAt(0).toUpperCase() + word.slice(1);
}

export function priorityLabel(priority: string): string {
  const word = priority.replace(/_/g, ' ');
  return word.charAt(0).toUpperCase() + word.slice(1);
}

export function deskStatusLabel(status: string, modelVersion?: string | null): string {
  if (status === 'contributed' && modelVersion?.endsWith('-agent-v1')) return 'Looked · agent';
  if (status === 'contributed') return 'Looked';
  if (status === 'abstained') return 'No data';
  return status.replace(/_/g, ' ');
}

export function queueAlert(whyItMatters: string): string {
  const text = whyItMatters.trim();
  if (/emergency[- ]access|corridor/i.test(text)) return 'Emergency access at risk';
  if (/spillback|congestion|queue/i.test(text)) return 'Approach is backing up';
  const first = text.split(/[.!?]/)[0]?.trim() || text;
  return first.length > 42 ? `${first.slice(0, 39).trim()}…` : first;
}

export const QUEUE_BADGE_ICONS: Record<string, string> = {
  Traffic: '/icons/traffic.svg?v=2',
  Parking: '/icons/parking.svg?v=2',
  'Tiger Transit': '/icons/tiger-transit.svg?v=2',
};

export function queueBadges(services: string[]): string[] {
  const seen = new Set<string>();
  const badges: string[] = [];
  for (const service of services) {
    const key = service.toLowerCase();
    const label = key.includes('tiger') || key.includes('transit')
      ? 'Tiger Transit'
      : key.includes('park')
        ? 'Parking'
        : key.includes('traffic') || key === 'ingress' || key === 'roads'
          ? 'Traffic'
          : null;
    if (!label || seen.has(label)) continue;
    seen.add(label);
    badges.push(label);
  }
  return badges.slice(0, 3);
}
