import { atlasAiConfig } from '../atlas/config.js';
import type { DeskAgentProfile } from '../profileTypes.js';
import { AQUA_LOCKED, AQUA_MODEL_CHOICES, AQUA_TOOL_CATALOG } from './catalog.js';

export function presentAquaProfile(profile: DeskAgentProfile) {
  const runtime = atlasAiConfig();
  return {
    deskCode: 'aqua' as const,
    name: 'AQUA',
    role: profile.role,
    backstory: profile.backstory,
    instructions: profile.instructions,
    llm: profile.llm,
    tools: AQUA_TOOL_CATALOG.map(item => ({
      ...item,
      enabled: profile.enabledTools.includes(item.name),
    })),
    policies: profile.policies,
    locked: AQUA_LOCKED,
    runtime: {
      enabled: runtime.enabled,
      host: runtime.baseUrl,
      keyConfigured: runtime.apiKey.length > 0,
      models: [...AQUA_MODEL_CHOICES],
    },
    updatedAt: profile.updatedAt,
  };
}

export type AquaProfileView = ReturnType<typeof presentAquaProfile>;
