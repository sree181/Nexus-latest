/**
 * ATLAS agent runtime. Off unless explicitly enabled with a key.
 *
 * Default host is Groq's free OpenAI-compatible endpoint. Llama 3.3/3.1 chat
 * models were retired on Groq in August 2026; gpt-oss-20b is the current
 * free-tier model that still does tool calling without built-in web/code tools.
 * Point ATLAS_AI_BASE_URL at Ollama (`http://127.0.0.1:11434/v1`) to run local.
 */
function truthy(value: string | undefined): boolean {
  return ['1', 'true', 'yes', 'on'].includes((value ?? '').trim().toLowerCase());
}

function numeric(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export interface AtlasAiConfig {
  enabled: boolean;
  baseUrl: string;
  apiKey: string;
  model: string;
  temperature: number;
  maxTurns: number;
  timeoutMs: number;
}

export function atlasAiConfig(env: NodeJS.ProcessEnv = process.env): AtlasAiConfig {
  const apiKey = (env.ATLAS_AI_API_KEY || env.GROQ_API_KEY || '').trim();
  const baseUrl = (env.ATLAS_AI_BASE_URL || 'https://api.groq.com/openai/v1').replace(/\/$/, '');
  return {
    enabled: truthy(env.ATLAS_AI_ENABLED) && apiKey.length > 0,
    baseUrl,
    apiKey,
    model: (env.ATLAS_AI_MODEL || 'openai/gpt-oss-20b').trim(),
    temperature: 0.1,
    maxTurns: Math.min(12, Math.floor(numeric(env.ATLAS_AI_MAX_TURNS, 8))),
    timeoutMs: Math.min(45_000, Math.floor(numeric(env.ATLAS_AI_TIMEOUT_MS, 20_000))),
  };
}

export function isAtlasAiEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return atlasAiConfig(env).enabled;
}

export const ATLAS_AGENT_VERSION = 'atlas-agent-v1';
