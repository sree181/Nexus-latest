import type { DetectionEvidence } from '../../detection/rules.js';
import { AQUA_ALLOWED_CONNECTORS } from '../desks.js';
import type { DeskPolicy } from '../profileTypes.js';
import { aquaActionTextFor, isAquaActionFamily } from './actions.js';

export interface AquaToolDef {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

export const AQUA_TOOLS: AquaToolDef[] = [
  {
    type: 'function',
    function: {
      name: 'list_aqua_evidence',
      description: 'List every observation in this snapshot that AQUA is allowed to read.',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_evidence',
      description: 'Read one permitted parking or transit row by id.',
      parameters: {
        type: 'object',
        properties: { id: { type: 'string', description: 'Evidence id from list_aqua_evidence.' } },
        required: ['id'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'search_policies',
      description: 'Search operator-loaded department, city, county, and state policy notes. Results are reference, never evidence.',
      parameters: {
        type: 'object',
        properties: {
          query: { type: 'string', description: 'Words to match in title, source, or body.' },
          jurisdiction: {
            type: 'string',
            enum: ['department', 'city', 'county', 'state'],
            description: 'Optional filter.',
          },
        },
        required: ['query'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_policy',
      description: 'Read one policy note by id. Do not cite this id as evidence.',
      parameters: {
        type: 'object',
        properties: { id: { type: 'string', description: 'Policy id, for example policy:dept-parking.' } },
        required: ['id'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'propose_action',
      description: 'Pick the only next step AQUA may propose. Must be one of confirm_lot_shuttle, hold_for_occupancy, note_shuttles_only.',
      parameters: {
        type: 'object',
        properties: {
          family: {
            type: 'string',
            enum: ['confirm_lot_shuttle', 'hold_for_occupancy', 'note_shuttles_only'],
          },
        },
        required: ['family'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'draft_finding',
      description: 'Commit AQUA’s finding. Cite only evidence ids returned by tools. End the turn after this.',
      parameters: {
        type: 'object',
        properties: {
          observation: { type: 'string' },
          interpretation: { type: 'string' },
          confidence: { type: 'number' },
          citedEvidenceIds: { type: 'array', items: { type: 'string' } },
          limitations: { type: 'string' },
        },
        required: ['observation', 'interpretation', 'confidence', 'citedEvidenceIds'],
        additionalProperties: false,
      },
    },
  },
];

export interface AquaDraft {
  observation: string;
  interpretation: string;
  confidence: number;
  citedEvidenceIds: string[];
  limitations?: string;
}

export interface AquaToolState {
  readIds: Set<string>;
  proposedAction: string | null;
  draft: AquaDraft | null;
}

export function emptyToolState(): AquaToolState {
  return { readIds: new Set(), proposedAction: null, draft: null };
}

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value);
}

function numeric(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function permitted(visible: DetectionEvidence[]): DetectionEvidence[] {
  return visible.filter(item => item.connectorCode !== null
    && (AQUA_ALLOWED_CONNECTORS as readonly string[]).includes(item.connectorCode));
}

function card(item: DetectionEvidence): Record<string, unknown> {
  const attrs = item.attributes;
  return {
    id: item.evidenceId,
    connector: item.connectorCode,
    summary: item.summary,
    observedAt: item.observedAt,
    routeName: attrs.routeName ?? null,
    equipmentId: attrs.equipmentId ?? null,
    inService: attrs.inService ?? null,
    load: attrs.load ?? null,
    capacity: attrs.capacity ?? null,
    availableUnits: attrs.availableUnits ?? null,
    currentDelayMinutes: attrs.currentDelayMinutes ?? null,
    occupancy: attrs.occupancy ?? attrs.occupancyPct ?? null,
  };
}

export function toolsForProfile(enabledTools: string[]): AquaToolDef[] {
  const allowed = new Set(enabledTools);
  return AQUA_TOOLS.filter(tool => allowed.has(tool.function.name));
}

export function executeAquaTool(
  name: string,
  rawArgs: unknown,
  visible: DetectionEvidence[],
  state: AquaToolState,
  policies: DeskPolicy[] = [],
): unknown {
  const allowed = permitted(visible);
  const args = rawArgs && typeof rawArgs === 'object' && !Array.isArray(rawArgs)
    ? rawArgs as Record<string, unknown>
    : {};

  if (name === 'list_aqua_evidence') {
    for (const item of allowed) state.readIds.add(item.evidenceId);
    return { count: allowed.length, evidence: allowed.slice(0, 24).map(card) };
  }

  if (name === 'get_evidence') {
    const id = text(args.id);
    const item = allowed.find(row => row.evidenceId === id);
    if (!item) return { error: 'unknown_or_forbidden', id };
    state.readIds.add(item.evidenceId);
    return {
      ...card(item),
      sourceName: item.sourceName,
      sourceEventId: item.sourceEventId,
      attributes: item.attributes,
    };
  }

  if (name === 'search_policies') {
    const query = text(args.query).trim().toLowerCase();
    const jurisdiction = text(args.jurisdiction).trim();
    if (query.length < 2) return { error: 'query_too_short' };
    const hits = policies
      .filter(policy => !jurisdiction || policy.jurisdiction === jurisdiction)
      .filter(policy => `${policy.title} ${policy.source} ${policy.body}`.toLowerCase().includes(query))
      .slice(0, 8)
      .map(policy => ({
        id: policy.id,
        title: policy.title,
        jurisdiction: policy.jurisdiction,
        source: policy.source,
        excerpt: policy.body.length > 280 ? `${policy.body.slice(0, 277).trim()}…` : policy.body,
        evidence: false,
      }));
    return { count: hits.length, policies: hits, note: 'Policy notes are reference. Cite evidence ids only.' };
  }

  if (name === 'get_policy') {
    const id = text(args.id);
    const policy = policies.find(item => item.id === id);
    if (!policy) return { error: 'unknown_policy', id };
    return { ...policy, evidence: false, note: 'Do not cite this id as evidence.' };
  }

  if (name === 'propose_action') {
    const family = text(args.family);
    if (!isAquaActionFamily(family)) {
      return { error: 'unknown_family', allowed: ['confirm_lot_shuttle', 'hold_for_occupancy', 'note_shuttles_only'] };
    }
    state.proposedAction = aquaActionTextFor(family);
    return { family, candidateAction: state.proposedAction };
  }

  if (name === 'draft_finding') {
    const cited = Array.isArray(args.citedEvidenceIds)
      ? args.citedEvidenceIds.map(value => text(value)).filter(Boolean)
      : [];
    const confidence = numeric(args.confidence);
    state.draft = {
      observation: text(args.observation),
      interpretation: text(args.interpretation),
      confidence: confidence ?? Number.NaN,
      citedEvidenceIds: cited,
      limitations: text(args.limitations) || undefined,
    };
    return { accepted: true, cited: cited.length };
  }

  return { error: 'unknown_tool', name };
}
