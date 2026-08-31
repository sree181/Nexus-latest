/** Charter wiring: a desk may only read the connectors it declares. */

export type WorkflowKind = 'source' | 'agent' | 'stakeholder' | 'decision';

export interface WorkflowSource {
  id: string;
  label: string;
  agency: string;
  aliases: string[];
}

export interface WorkflowAgent {
  id: string;
  label: string;
  role: string;
  connectors: string[];
}

export const WORKFLOW_SOURCES: WorkflowSource[] = [
  { id: 'tomtom-traffic-flow-v1', label: 'TomTom flow', agency: 'TomTom', aliases: ['tomtom'] },
  { id: 'aldot-algo-traffic-v1', label: 'ALDOT ALGO', agency: 'ALDOT', aliases: ['aldot-algo', 'algo'] },
  { id: 'aldot-traffic-counts-v1', label: 'ALDOT counts', agency: 'ALDOT', aliases: ['aldot-traffic-counts', 'counts'] },
  { id: 'auburn-eta-spot-v1', label: 'Tiger Transit', agency: 'Transit', aliases: ['eta-spot', 'tiger', 'transit'] },
  { id: 'auburn-parking-occupancy-v1', label: 'Parking occupancy', agency: 'Parking', aliases: ['parking'] },
  { id: 'nws-weather-alerts-v1', label: 'NWS alerts', agency: 'NWS', aliases: ['nws', 'weather'] },
  { id: 'auburn-emergency-access-v1', label: 'Emergency access', agency: 'Event command', aliases: ['emergency'] },
  { id: 'coa-road-closures-v1', label: 'City closures', agency: 'City', aliases: ['closure', 'coa'] },
  { id: 'usgs-natural-hazards-v1', label: 'USGS hazards', agency: 'USGS', aliases: ['usgs'] },
  { id: 'nexus-siem-alerts-v1', label: 'SIEM alerts', agency: 'Nexus', aliases: ['siem'] },
];

export const WORKFLOW_AGENTS: WorkflowAgent[] = [
  { id: 'atlas', label: 'ATLAS', role: 'Traffic', connectors: ['aldot-algo-traffic-v1', 'tomtom-traffic-flow-v1', 'aldot-traffic-counts-v1'] },
  { id: 'aqua', label: 'AQUA', role: 'Parking and transit', connectors: ['auburn-eta-spot-v1', 'auburn-parking-occupancy-v1'] },
  { id: 'sentinel', label: 'SENTINEL', role: 'Public safety', connectors: ['nws-weather-alerts-v1', 'auburn-emergency-access-v1'] },
  { id: 'phoenix', label: 'PHOENIX', role: 'Emergency routes', connectors: ['auburn-emergency-access-v1', 'coa-road-closures-v1'] },
  { id: 'forge', label: 'FORGE', role: 'Roads and utilities', connectors: ['coa-road-closures-v1', 'usgs-natural-hazards-v1'] },
  { id: 'echo', label: 'ECHO', role: 'Communications', connectors: ['aldot-algo-traffic-v1', 'nexus-siem-alerts-v1'] },
];

export const PALETTE: { kind: WorkflowKind; id: string; label: string; note: string }[] = [
  ...WORKFLOW_SOURCES.map(item => ({ kind: 'source' as const, id: item.id, label: item.label, note: item.agency })),
  ...WORKFLOW_AGENTS.map(item => ({ kind: 'agent' as const, id: item.id, label: item.label, note: item.role })),
  { kind: 'stakeholder', id: 'stakeholder', label: 'Stakeholder', note: 'Named human' },
  { kind: 'decision', id: 'decision', label: 'Decision', note: 'Signed record' },
];

export function sourceIsLive(source: WorkflowSource, haystack: string): boolean {
  const blob = haystack.toLowerCase();
  if (blob.includes(source.id.toLowerCase())) return true;
  return source.aliases.some(alias => blob.includes(alias));
}
