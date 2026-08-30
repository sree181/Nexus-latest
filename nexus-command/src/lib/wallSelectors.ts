import type { GraphEdge, GraphNode, GraphSnapshot } from '../graphTypes';
import type { Incident, OperationalObservation, SourceHealth } from '../operationalTypes';
import { DESK_ICONS, DESK_ORDER, deskCallsign, deskName } from '../uiCopy';

export type WallScreen = 'operations' | 'lineage' | 'coordination' | 'mobility';

export interface WallFeed {
  name: string;
  lastSeenAt: string | null;
  tone: 'ok' | 'stale' | 'danger';
}

export interface WallDesk {
  code: string;
  name: string;
  role: string;
  icon: string;
  staffed: boolean;
  operator: string | null;
  lastTitle: string;
  lastAt: string | null;
}

export interface ImpactRow {
  category: string;
  figure: number;
  unit: string;
  named: boolean;
}

export interface LineageItem {
  id: string;
  title: string;
  status: string;
  tone: 'accent' | 'ok' | 'muted' | 'danger' | 'info';
  count?: number;
  parents: string[];
  children: string[];
}

export interface LineageStage {
  key: string;
  label: string;
  count: number;
  items: LineageItem[];
  allItems: LineageItem[];
  more: number;
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, ' ').trim();
}

function connected(source: SourceHealth): boolean {
  return source.connectionStatus ? source.connectionStatus === 'connected' : source.status !== 'unavailable';
}

function isProcessStatement(text: string): boolean {
  return /require command review|authoritative mobility observations/i.test(text);
}

function placeFromTitle(title: string): string | null {
  const afterDash = title.split(/[—–]/)[1]?.trim();
  return afterDash && afterDash.length <= 28 ? afterDash : null;
}

export function deriveImpact(incident: Incident): string {
  if (incident.impact?.trim()) return incident.impact.trim();
  const place = placeFromTitle(incident.title);
  if (/emergency[- ]access|corridor/i.test(incident.whyItMatters)) {
    return place ? `Emergency access at risk: ${place}` : 'Emergency access at risk';
  }
  const first = incident.whyItMatters.split(/[.!?]/)[0]?.trim();
  if (first && first.length <= 44 && !isProcessStatement(first)) return first;
  return incident.title;
}

export function situationFromIncident(incident: Incident | null): string {
  if (!incident) return 'No incident requires a decision';
  return deriveImpact(incident);
}

export function subtitleFromIncident(incident: Incident | null, lastClearedAt: string | null): string {
  if (!incident) {
    return lastClearedAt
      ? `Last cleared ${new Date(lastClearedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}`
      : 'No named rule has opened an incident';
  }
  return incident.title;
}

export function streetLabel(incident: Incident | null): string {
  if (!incident) return 'Operating area';
  const haystack = `${incident.title} ${incident.whyItMatters} ${incident.whatChanged}`;
  const match = haystack.match(/\b(?:on|at)\s+([A-Z][A-Za-z0-9 .'-]{3,40})/);
  if (match?.[1]) return match[1].replace(/\s+$/, '');
  const afterDash = incident.title.split(/[—–]/)[1]?.trim();
  if (afterDash && afterDash.length <= 40) {
    return afterDash.charAt(0).toUpperCase() + afterDash.slice(1);
  }
  return incident.title;
}

export function feedPills(sources: SourceHealth[], now = Date.now()): WallFeed[] {
  return sources.slice(0, 9).map(source => {
    const lastSeenAt = source.lastEventObservedAt || source.lastSuccessAt;
    const age = lastSeenAt ? (now - Date.parse(lastSeenAt)) / 1000 : null;
    let tone: WallFeed['tone'] = 'ok';
    if (!connected(source) || age === null) tone = 'danger';
    else if (age > 300) tone = 'danger';
    else if (age > 60) tone = 'stale';
    return { name: source.name, lastSeenAt, tone };
  });
}

export function liveFeedCount(sources: SourceHealth[]): { live: number; total: number } {
  return { live: sources.filter(connected).length, total: sources.length };
}

const WALL_ROLES: Record<string, string> = {
  atlas: 'Traffic',
  aqua: 'Parking',
  sentinel: 'Public safety',
  phoenix: 'Emergency routes',
  forge: 'Roads',
  echo: 'Communications',
};

export function impactRows(observations: OperationalObservation[], incident: Incident | null): ImpactRow[] {
  const transit = observations.filter(item => item.sourceCode.includes('transit')).length;
  const closures = observations.filter(item => item.sourceCode.includes('closure')).length;
  const parking = observations.filter(item => item.sourceCode.includes('parking') || item.sourceCode.includes('lot')).length;
  const services = incident?.affectedServices ?? [];
  return [
    {
      category: 'Transit',
      figure: transit,
      unit: 'vehicles',
      named: services.some(item => /transit|shuttle/i.test(item)),
    },
    {
      category: 'Parking',
      figure: parking,
      unit: 'rows',
      named: services.some(item => /park/i.test(item)),
    },
    {
      category: 'Signals',
      figure: closures,
      unit: 'closures',
      named: services.some(item => /signal|closure|road/i.test(item)),
    },
  ];
}

export function deskStaffing(
  findings: Array<{ agentCode: string; status: string; observation?: string; interpretation?: string; createdAt?: string }>,
  operator: string | null = null,
): WallDesk[] {
  const byCode = new Map(findings.map(item => [item.agentCode.toLowerCase(), item]));
  return DESK_ORDER.map(code => {
    const finding = byCode.get(code);
    const last = (finding?.observation || finding?.interpretation || '').split(/[.!?]/)[0]?.trim() || '';
    return {
      code,
      name: deskCallsign(code),
      role: WALL_ROLES[code] ?? deskName(code),
      icon: DESK_ICONS[code],
      staffed: finding?.status === 'contributed',
      operator: finding?.status === 'contributed' ? operator : null,
      lastTitle: last,
      lastAt: finding?.createdAt ?? null,
    };
  });
}

function neighbors(edges: GraphEdge[]) {
  const parents = new Map<string, string[]>();
  const children = new Map<string, string[]>();
  for (const edge of edges) {
    children.set(edge.fromNodeId, [...(children.get(edge.fromNodeId) ?? []), edge.toNodeId]);
    parents.set(edge.toNodeId, [...(parents.get(edge.toNodeId) ?? []), edge.fromNodeId]);
  }
  return { parents, children };
}

function itemFromNode(node: GraphNode, parents: string[], children: string[]): LineageItem {
  const historical = node.qualityFlags.includes('historical') || node.qualityFlags.includes('closed');
  const incidentStatus = String(node.state.status ?? '');
  let status = historical ? 'Closed' : node.qualityFlags.includes('current') ? 'Current' : 'Recorded';
  let tone: LineageItem['tone'] = historical ? 'muted' : node.nodeType === 'decision' ? 'accent' : node.qualityFlags.includes('current') ? 'ok' : 'info';
  if (node.nodeType === 'incident') {
    if (['closed', 'resolved'].includes(incidentStatus)) {
      status = 'Closed';
      tone = 'muted';
    } else if (['new', 'triaged', 'active', 'monitoring'].includes(incidentStatus)) {
      status = 'Open';
      tone = 'accent';
    } else {
      status = 'Recorded';
      tone = 'ok';
    }
  }
  if (node.nodeType === 'decision') {
    const decided = String(node.state.decidedAt ?? node.updatedAt);
    status = Number.isNaN(Date.parse(decided)) ? node.label : new Date(decided).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  }
  return {
    id: node.nodeId,
    title: node.label,
    status,
    tone,
    parents,
    children,
  };
}

function dedupeItems(items: LineageItem[]): LineageItem[] {
  const grouped = new Map<string, LineageItem>();
  for (const item of items) {
    const key = normalize(item.title);
    const existing = grouped.get(key);
    if (!existing) grouped.set(key, { ...item, count: 1 });
    else {
      existing.count = (existing.count ?? 1) + 1;
      existing.parents = [...new Set([...existing.parents, ...item.parents])];
      existing.children = [...new Set([...existing.children, ...item.children])];
    }
  }
  return [...grouped.values()];
}

export function lineageStages(snapshot: GraphSnapshot | null, maxCards = 4): LineageStage[] {
  const nodes = snapshot?.nodes ?? [];
  const edges = snapshot?.edges ?? [];
  const { parents, children } = neighbors(edges);
  const byType = (type: string) => nodes
    .filter(node => node.nodeType === type)
    .sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt))
    .map(node => itemFromNode(node, parents.get(node.nodeId) ?? [], children.get(node.nodeId) ?? []));

  const findings = byType('finding');
  const agencyGroups = new Map<string, LineageItem>();
  for (const item of findings) {
    const agency = item.title.split('·')[0]?.trim() || item.title;
    const current = agencyGroups.get(agency);
    if (!current) {
      agencyGroups.set(agency, { ...item, title: agency, status: '1', count: 1 });
    } else {
      current.count = (current.count ?? 1) + 1;
      current.status = String(current.count);
      current.parents = [...new Set([...current.parents, ...item.parents])];
      current.children = [...new Set([...current.children, ...item.children])];
    }
  }

  const definitions: Array<{ key: string; label: string; items: LineageItem[] }> = [
    { key: 'evidence', label: 'Evidence', items: byType('evidence') },
    { key: 'finding', label: 'Findings', items: [...agencyGroups.values()] },
    { key: 'incident', label: 'Incidents', items: byType('incident') },
    { key: 'recommendation', label: 'Recommendations', items: dedupeItems(byType('recommendation')) },
    { key: 'decision', label: 'Decision', items: byType('decision') },
    { key: 'commitment', label: 'Commitments', items: byType('commitment') },
    { key: 'verification', label: 'Verified', items: byType('verification') },
  ];

  return definitions.map(stage => {
    const visible = stage.key === 'finding' ? 8 : maxCards;
    return {
      key: stage.key,
      label: stage.label,
      count: stage.key === 'finding' ? findings.length : stage.items.reduce((sum, item) => sum + (item.count ?? 1), 0),
      items: stage.items.slice(0, visible),
      allItems: stage.items,
      more: Math.max(0, stage.items.length - visible),
    };
  });
}

export function walkPath(id: string, stages: LineageStage[]): Set<string> {
  const items = stages.flatMap(stage => stage.allItems);
  const byId = new Map(items.map(item => [item.id, item]));
  const keep = new Set<string>([id]);
  const walk = (start: string, key: 'parents' | 'children') => {
    const queue = [start];
    while (queue.length) {
      const current = byId.get(queue.shift()!);
      if (!current) continue;
      for (const next of current[key]) {
        if (keep.has(next)) continue;
        keep.add(next);
        queue.push(next);
      }
    }
  };
  walk(id, 'parents');
  walk(id, 'children');
  return keep;
}
