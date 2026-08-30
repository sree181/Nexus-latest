import { AQUA_ALLOWED_CONNECTORS, AQUA_BOUNDARY, AQUA_MISSION } from '../desks.js';
import type { DeskAgentProfile, DeskToolCatalogItem } from '../profileTypes.js';
import { ATLAS_MODEL_CHOICES } from '../atlas/catalog.js';

export const AQUA_TOOL_CATALOG: DeskToolCatalogItem[] = [
  {
    name: 'list_aqua_evidence',
    label: 'List parking and transit evidence',
    description: 'See shuttle and lot observations AQUA is allowed to read.',
    required: true,
  },
  {
    name: 'get_evidence',
    label: 'Read one evidence row',
    description: 'Open a single permitted parking or transit record.',
    required: false,
  },
  {
    name: 'search_policies',
    label: 'Search policies',
    description: 'Search department, city, county, and state parking or transit notes.',
    required: false,
  },
  {
    name: 'get_policy',
    label: 'Read one policy',
    description: 'Read a full policy note. Policy is reference, never evidence.',
    required: false,
  },
  {
    name: 'propose_action',
    label: 'Propose a next step',
    description: 'Pick a playbook-safe family. AQUA cannot change a schedule or lot policy.',
    required: true,
  },
  {
    name: 'draft_finding',
    label: 'Draft the finding',
    description: 'Write the cited finding the operator reviews.',
    required: true,
  },
];

export const AQUA_MODEL_CHOICES = ATLAS_MODEL_CHOICES;

export function defaultAquaProfile(): DeskAgentProfile {
  return {
    deskCode: 'aqua',
    role: 'Parking and transit desk for Auburn remote lots, curb, and Tiger Transit staging.',
    backstory: [
      'AQUA is the parking and transit reviewer on Nexus Coordinate.',
      'It watches Tiger Transit vehicle positions and, when a partner feed exists, lot occupancy.',
      'It knows Parking & Transit owns schedules and lot policy, and that occupancy is often not connected.',
      'It does not speak for corridor speed, weather, or emergency access.',
    ].join(' '),
    instructions: [
      'Read the permitted feeds before you write.',
      'If lot occupancy is not connected, say so plainly. Do not infer a full lot from shuttle delay.',
      'If a policy note bears on staging, say so in the interpretation and still cite only evidence ids.',
    ].join(' '),
    llm: {
      model: 'openai/gpt-oss-20b',
      temperature: 0.1,
      maxTurns: 8,
      timeoutMs: 20_000,
    },
    enabledTools: AQUA_TOOL_CATALOG.map(item => item.name),
    policies: [
      {
        id: 'policy:dept-parking',
        title: 'Lot policy stays with Parking & Transit',
        jurisdiction: 'department',
        source: 'Nexus operating note — Parking & Transit Operations boundary',
        body: 'Parking & Transit sets lot hours, remote-lot use, and shuttle schedules. AQUA may ask them to confirm state. It may not open a lot, waive a fee, or rewrite a schedule.',
      },
      {
        id: 'policy:city-ada',
        title: 'ADA loading is preserved',
        jurisdiction: 'city',
        source: 'Nexus operating note — City ADA loading constraint',
        body: 'Any remote-lot or staging recommendation must preserve ADA loading. AQUA cannot treat an unmarked curb as available ADA space. A human parking owner confirms staging.',
      },
      {
        id: 'policy:occupancy-gap',
        title: 'Occupancy is partner-gated',
        jurisdiction: 'department',
        source: 'Nexus operating note — parking occupancy feed',
        body: 'Lot occupancy is not a public feed. When it is not connected, AQUA reasons from shuttle positions alone and must say that a full lot cannot be distinguished from a shuttle problem.',
      },
    ],
    updatedAt: new Date(0).toISOString(),
  };
}

export const AQUA_LOCKED = {
  mission: AQUA_MISSION,
  boundary: AQUA_BOUNDARY,
  allowedConnectors: [...AQUA_ALLOWED_CONNECTORS],
  actionFamilies: ['confirm_lot_shuttle', 'hold_for_occupancy', 'note_shuttles_only'],
};
