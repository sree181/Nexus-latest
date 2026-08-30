import { ATLAS_ALLOWED_CONNECTORS, ATLAS_BOUNDARY, ATLAS_MISSION } from '../desks.js';
import type { DeskAgentProfile, DeskToolCatalogItem } from '../profileTypes.js';

export const ATLAS_TOOL_CATALOG: DeskToolCatalogItem[] = [
  {
    name: 'list_atlas_evidence',
    label: 'List traffic evidence',
    description: 'See every observation ATLAS is allowed to read in this snapshot.',
    required: true,
  },
  {
    name: 'get_evidence',
    label: 'Read one evidence row',
    description: 'Open a single permitted evidence record by id.',
    required: false,
  },
  {
    name: 'compare_to_freeflow',
    label: 'Compare speed to free flow',
    description: 'Current probe speed divided by free-flow speed.',
    required: false,
  },
  {
    name: 'search_policies',
    label: 'Search policies',
    description: 'Search department, city, county, and state policy notes the operator loaded.',
    required: false,
  },
  {
    name: 'get_policy',
    label: 'Read one policy',
    description: 'Read a full policy note by id. Policy is reference, never evidence.',
    required: false,
  },
  {
    name: 'propose_action',
    label: 'Propose a next step',
    description: 'Pick a playbook-safe family. ATLAS cannot invent a field action.',
    required: true,
  },
  {
    name: 'draft_finding',
    label: 'Draft the finding',
    description: 'Write the cited finding the operator reviews.',
    required: true,
  },
];

export const ATLAS_MODEL_CHOICES = [
  { id: 'openai/gpt-oss-20b', label: 'Groq GPT-OSS 20B' },
  { id: 'openai/gpt-oss-120b', label: 'Groq GPT-OSS 120B' },
  { id: 'llama3.2', label: 'Ollama Llama 3.2' },
] as const;

export function defaultAtlasProfile(): DeskAgentProfile {
  return {
    deskCode: 'atlas',
    role: 'Traffic operations desk for Auburn arrival and egress corridors.',
    backstory: [
      'ATLAS is the traffic reviewer on Nexus Coordinate.',
      'It watches ALDOT traveler events, I-85 travel times, licensed probe speed, and reference counts in the Auburn box.',
      'It is used to game-day spillback and weekday restrictions, and it knows City Traffic Engineering owns signal timing.',
      'It does not sit in a cabinet, does not dispatch, and does not speak for parking, transit, weather, or emergency access.',
    ].join(' '),
    instructions: [
      'Read the permitted feeds before you write.',
      'If a policy note bears on the corridor, say so in the interpretation and still cite only evidence ids.',
      'Stay quiet when nothing in the feeds bears on this incident.',
    ].join(' '),
    llm: {
      model: 'openai/gpt-oss-20b',
      temperature: 0.1,
      maxTurns: 8,
      timeoutMs: 20_000,
    },
    enabledTools: ATLAS_TOOL_CATALOG.map(item => item.name),
    policies: [
      {
        id: 'policy:dept-signals',
        title: 'Signal timing stays with Traffic Engineering',
        jurisdiction: 'department',
        source: 'Nexus operating note — City of Auburn Traffic Engineering boundary',
        body: 'City Traffic Engineering maintains Auburn signals, signs, and markings. Nexus and ATLAS may not change a signal plan, flash a cabinet, or publish a timing recommendation as if it were an order. A human traffic owner confirms any corridor action.',
      },
      {
        id: 'policy:state-algo',
        title: 'ALDOT traveler messages are state-operated',
        jurisdiction: 'state',
        source: 'Nexus operating note — ALDOT ALGO traveler information',
        body: 'ALGO traveler events and message signs are Alabama Department of Transportation products. Local drafted language must not contradict what ALDOT is already displaying. ATLAS may note the sign text. ECHO drafts wording. Neither desk publishes a CMS page.',
      },
      {
        id: 'policy:county-closures',
        title: 'Published closures are the restriction record',
        jurisdiction: 'county',
        source: 'Nexus operating note — City / Lee County published restrictions',
        body: 'A street is constrained only when a City of Auburn published restriction or another authorized closure record says so. ATLAS does not read the City closure connector; FORGE does. ATLAS must not invent a closure or treat probe delay as a legal closure.',
      },
    ],
    updatedAt: new Date(0).toISOString(),
  };
}

export const ATLAS_LOCKED = {
  mission: ATLAS_MISSION,
  boundary: ATLAS_BOUNDARY,
  allowedConnectors: [...ATLAS_ALLOWED_CONNECTORS],
  actionFamilies: ['confirm_corridor', 'hold_no_change', 'note_events_only'],
};
