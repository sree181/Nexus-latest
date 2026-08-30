import type { DetectionEvidence } from '../../detection/rules.js';
import { ATLAS_ALLOWED_CONNECTORS } from '../desks.js';
import type { DeskPolicy } from '../profileTypes.js';
import { actionTextFor, isAtlasActionFamily } from './actions.js';

export interface AtlasToolDef {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

export const ATLAS_TOOLS: AtlasToolDef[] = [
  {
    type: 'function',
    function: {
      name: 'list_atlas_evidence',
      description: 'List every observation in this snapshot that ATLAS is allowed to read.',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_evidence',
      description: 'Read one permitted evidence row by id.',
      parameters: {
        type: 'object',
        properties: { id: { type: 'string', description: 'Evidence id from list_atlas_evidence.' } },
        required: ['id'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'compare_to_freeflow',
      description: 'Compare current probe speed to free-flow speed for one row.',
      parameters: {
        type: 'object',
        properties: { id: { type: 'string', description: 'Evidence id from list_atlas_evidence.' } },
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
        properties: { id: { type: 'string', description: 'Policy id, for example policy:state-algo.' } },
        required: ['id'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'propose_action',
      description: 'Pick the only next step ATLAS may propose. Must be one of confirm_corridor, hold_no_change, note_events_only.',
      parameters: {
        type: 'object',
        properties: {
          family: {
            type: 'string',
            enum: ['confirm_corridor', 'hold_no_change', 'note_events_only'],
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
      description: 'Commit ATLAS’s finding. Cite only evidence ids returned by tools. End the turn after this.',
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

export interface AtlasDraft {
  observation: string;
  interpretation: string;
  confidence: number;
  citedEvidenceIds: string[];
  limitations?: string;
}

export interface AtlasToolState {
  readIds: Set<string>;
  proposedAction: string | null;
  draft: AtlasDraft | null;
}

export function emptyToolState(): AtlasToolState {
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
    && (ATLAS_ALLOWED_CONNECTORS as readonly string[]).includes(item.connectorCode));
}

function card(item: DetectionEvidence): Record<string, unknown> {
  const attrs = item.attributes;
  return {
    id: item.evidenceId,
    connector: item.connectorCode,
    layer: attrs.layer ?? null,
    summary: item.summary,
    observedAt: item.observedAt,
    congestionLevel: attrs.congestionLevel ?? null,
    currentSpeedMph: attrs.currentSpeedMph ?? null,
    freeFlowSpeedMph: attrs.freeFlowSpeedMph ?? null,
    eventType: attrs.eventType ?? attrs.kind ?? null,
  };
}

export function toolsForProfile(enabledTools: string[]): AtlasToolDef[] {
  const allowed = new Set(enabledTools);
  return ATLAS_TOOLS.filter(tool => allowed.has(tool.function.name));
}

export function executeAtlasTool(
  name: string,
  rawArgs: unknown,
  visible: DetectionEvidence[],
  state: AtlasToolState,
  policies: DeskPolicy[] = [],
): unknown {
  const allowed = permitted(visible);
  const args = rawArgs && typeof rawArgs === 'object' && !Array.isArray(rawArgs)
    ? rawArgs as Record<string, unknown>
    : {};

  if (name === 'list_atlas_evidence') {
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

  if (name === 'compare_to_freeflow') {
    const id = text(args.id);
    const item = allowed.find(row => row.evidenceId === id);
    if (!item) return { error: 'unknown_or_forbidden', id };
    state.readIds.add(item.evidenceId);
    const current = numeric(item.attributes.currentSpeedMph);
    const free = numeric(item.attributes.freeFlowSpeedMph);
    if (current === null || free === null || free <= 0) {
      return { id, comparable: false, reason: 'This row has no current and free-flow speed pair.' };
    }
    const ratio = current / free;
    return {
      id,
      comparable: true,
      currentSpeedMph: current,
      freeFlowSpeedMph: free,
      ratio: Math.round(ratio * 1000) / 1000,
      belowReviewThreshold: ratio < 0.6,
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
    if (!isAtlasActionFamily(family)) {
      return { error: 'unknown_family', allowed: ['confirm_corridor', 'hold_no_change', 'note_events_only'] };
    }
    state.proposedAction = actionTextFor(family);
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
