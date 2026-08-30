import { atlasAiConfig } from './config.js';
import { ATLAS_LOCKED, ATLAS_MODEL_CHOICES, ATLAS_TOOL_CATALOG } from './catalog.js';
import type { DeskAgentProfile } from '../profileTypes.js';

export function presentAtlasProfile(profile: DeskAgentProfile) {
  const runtime = atlasAiConfig();
  return {
    deskCode: 'atlas' as const,
    name: 'ATLAS',
    role: profile.role,
    backstory: profile.backstory,
    instructions: profile.instructions,
    llm: profile.llm,
    tools: ATLAS_TOOL_CATALOG.map(item => ({
      ...item,
      enabled: profile.enabledTools.includes(item.name),
    })),
    policies: profile.policies,
    locked: ATLAS_LOCKED,
    runtime: {
      enabled: runtime.enabled,
      host: runtime.baseUrl,
      keyConfigured: runtime.apiKey.length > 0,
      models: [...ATLAS_MODEL_CHOICES],
    },
    updatedAt: profile.updatedAt,
  };
}

export type AtlasProfileView = ReturnType<typeof presentAtlasProfile>;
