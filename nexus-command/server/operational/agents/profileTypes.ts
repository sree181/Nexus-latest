export const POLICY_JURISDICTIONS = ['department', 'city', 'county', 'state'] as const;
export type PolicyJurisdiction = (typeof POLICY_JURISDICTIONS)[number];

export interface DeskPolicy {
  id: string;
  title: string;
  jurisdiction: PolicyJurisdiction;
  source: string;
  body: string;
}

export interface DeskLlmSettings {
  model: string;
  temperature: number;
  maxTurns: number;
  timeoutMs: number;
}

export interface DeskAgentProfile {
  deskCode: string;
  role: string;
  backstory: string;
  instructions: string;
  llm: DeskLlmSettings;
  enabledTools: string[];
  policies: DeskPolicy[];
  updatedAt: string;
}

export interface DeskToolCatalogItem {
  name: string;
  label: string;
  description: string;
  required: boolean;
}
