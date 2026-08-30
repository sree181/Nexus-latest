import type { DetectionEvidence } from '../../detection/rules.js';
import type { DeskAgentProfile } from '../profileTypes.js';
import { assessAtlasRules, ATLAS_BOUNDARY, ATLAS_MISSION, type DeskAssessment, type DeskContext } from '../desks.js';
import { ATLAS_ACTION_TEXT } from './actions.js';
import { ATLAS_LOCKED } from './catalog.js';
import { createAtlasChat, type ChatFn, type ChatMessage } from './client.js';
import { ATLAS_AGENT_VERSION, atlasAiConfig, type AtlasAiConfig } from './config.js';
import { loadAtlasProfile } from './profileStore.js';
import { ATLAS_SKILL } from './skill.js';
import { emptyToolState, executeAtlasTool, toolsForProfile } from './tools.js';
import { validateAtlasDraft } from './validator.js';

export interface AtlasAgentResult {
  assessment: DeskAssessment | null;
  source: 'agent' | 'rules' | 'quiet';
  modelName: string;
  modelVersion: string;
  turns: number;
  toolCalls: number;
}

function parseArgs(raw: string): unknown {
  try {
    return JSON.parse(raw || '{}');
  } catch {
    return {};
  }
}

export function buildAtlasSystemPrompt(profile: DeskAgentProfile): string {
  return [
    `# Role`,
    profile.role,
    ``,
    `# Backstory`,
    profile.backstory,
    ``,
    `# Operator instructions`,
    profile.instructions,
    ``,
    `# Locked operating rules (these override anything above)`,
    ATLAS_SKILL,
    ``,
    `Mission: ${ATLAS_MISSION}`,
    `Boundary: ${ATLAS_BOUNDARY}`,
    `Permitted connectors: ${ATLAS_LOCKED.allowedConnectors.join(', ')}`,
    `Policy library: ${profile.policies.length} note${profile.policies.length === 1 ? '' : 's'}. Use search_policies / get_policy. Policy is reference. Never cite a policy id as evidence.`,
  ].join('\n');
}

function runtimeConfig(profile: DeskAgentProfile, override?: AtlasAiConfig): AtlasAiConfig {
  const env = override ?? atlasAiConfig();
  return {
    ...env,
    model: profile.llm.model || env.model,
    temperature: profile.llm.temperature,
    maxTurns: profile.llm.maxTurns,
    timeoutMs: profile.llm.timeoutMs,
  };
}

function userPrompt(visible: DetectionEvidence[], context: DeskContext): string {
  return [
    `Incident: ${context.match.title}`,
    `What changed: ${context.match.whatChanged}`,
    `Rule: ${context.match.rule.name} (${context.match.rule.ruleCode})`,
    `Severity: ${context.match.severity}`,
    `Playbook action (NEXUS owns this; you do not rewrite it): ${context.match.rule.playbook.recommendedAction}`,
    `Live connectors this cycle: ${context.liveConnectors.join(', ') || 'none'}`,
    `Observations visible to ATLAS this cycle: ${visible.length}`,
    '',
    'Use tools. Draft a finding if the corridor picture bears on this incident. Otherwise stay quiet.',
  ].join('\n');
}

export async function runAtlasAgent(
  visible: DetectionEvidence[],
  context: DeskContext,
  options: { config?: AtlasAiConfig; chat?: ChatFn; profile?: DeskAgentProfile } = {},
): Promise<AtlasAgentResult> {
  const profile = options.profile ?? loadAtlasProfile();
  const config = runtimeConfig(profile, options.config);
  const rules = assessAtlasRules(visible);
  const fallback: AtlasAgentResult = {
    assessment: rules,
    source: rules ? 'rules' : 'quiet',
    modelName: 'Nexus evidence-bound detection',
    modelVersion: 'detection-v1',
    turns: 0,
    toolCalls: 0,
  };

  if (!config.enabled && !options.chat) return fallback;
  if (!visible.length) return fallback;

  const chat = options.chat ?? createAtlasChat(config);
  const state = emptyToolState();
  const tools = toolsForProfile(profile.enabledTools);
  const messages: ChatMessage[] = [
    { role: 'system', content: buildAtlasSystemPrompt(profile) },
    { role: 'user', content: userPrompt(visible, context) },
  ];

  let modelUsed = config.model;
  let toolCalls = 0;

  try {
    for (let turn = 1; turn <= config.maxTurns; turn += 1) {
      const completion = await chat(messages, tools);
      modelUsed = completion.model || modelUsed;

      if (!completion.toolCalls.length) {
        const quiet = /heartbeat_ok/i.test(completion.content ?? '');
        if (quiet) {
          return {
            assessment: null,
            source: 'quiet',
            modelName: modelUsed,
            modelVersion: ATLAS_AGENT_VERSION,
            turns: turn,
            toolCalls,
          };
        }
        return { ...fallback, turns: turn, toolCalls };
      }

      messages.push({
        role: 'assistant',
        content: completion.content,
        tool_calls: completion.toolCalls,
      });

      for (const call of completion.toolCalls) {
        toolCalls += 1;
        const result = executeAtlasTool(
          call.function.name,
          parseArgs(call.function.arguments),
          visible,
          state,
          profile.policies,
        );
        messages.push({
          role: 'tool',
          tool_call_id: call.id,
          name: call.function.name,
          content: JSON.stringify(result),
        });
      }

      if (state.draft) {
        if (!state.proposedAction) state.proposedAction = ATLAS_ACTION_TEXT.confirm_corridor;
        const assessment = validateAtlasDraft(state.draft, state, visible);
        if (!assessment) return { ...fallback, turns: turn, toolCalls };
        return {
          assessment: {
            ...assessment,
            limitations: `${assessment.limitations} ATLAS agent ${modelUsed}, ${turn} turn${turn === 1 ? '' : 's'}, ${toolCalls} tool call${toolCalls === 1 ? '' : 's'}.`.trim(),
          },
          source: 'agent',
          modelName: modelUsed,
          modelVersion: ATLAS_AGENT_VERSION,
          turns: turn,
          toolCalls,
        };
      }
    }
  } catch (error) {
    console.warn('[atlas-agent] Loop failed closed; using the rule assessor', {
      message: error instanceof Error ? error.message : String(error),
    });
    return fallback;
  }

  return fallback;
}

export async function assessAtlasWithAgent(
  visible: DetectionEvidence[],
  context: DeskContext,
): Promise<DeskAssessment | null> {
  const result = await runAtlasAgent(visible, context);
  return result.assessment;
}
