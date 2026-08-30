import type { DetectionEvidence } from '../../detection/rules.js';
import type { DeskAgentProfile } from '../profileTypes.js';
import { assessAquaRules, AQUA_BOUNDARY, AQUA_MISSION, type DeskAssessment, type DeskContext } from '../desks.js';
import { createDeskChat, type ChatFn, type ChatMessage } from '../llmClient.js';
import { atlasAiConfig, type AtlasAiConfig } from '../atlas/config.js';
import { AQUA_ACTION_TEXT } from './actions.js';
import { AQUA_LOCKED } from './catalog.js';
import { loadAquaProfile } from './profileStore.js';
import { AQUA_SKILL } from './skill.js';
import { emptyToolState, executeAquaTool, toolsForProfile } from './tools.js';
import { validateAquaDraft } from './validator.js';

export const AQUA_AGENT_VERSION = 'aqua-agent-v1';

export interface AquaAgentResult {
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

export function buildAquaSystemPrompt(profile: DeskAgentProfile): string {
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
    AQUA_SKILL,
    ``,
    `Mission: ${AQUA_MISSION}`,
    `Boundary: ${AQUA_BOUNDARY}`,
    `Permitted connectors: ${AQUA_LOCKED.allowedConnectors.join(', ')}`,
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
    `Occupancy feed connected: ${context.liveConnectors.includes('auburn-parking-occupancy-v1') ? 'yes' : 'no'}`,
    `Observations visible to AQUA this cycle: ${visible.length}`,
    '',
    'Use tools. Draft a finding if parking or shuttle state bears on this incident. Otherwise stay quiet.',
  ].join('\n');
}

export async function runAquaAgent(
  visible: DetectionEvidence[],
  context: DeskContext,
  options: { config?: AtlasAiConfig; chat?: ChatFn; profile?: DeskAgentProfile } = {},
): Promise<AquaAgentResult> {
  const profile = options.profile ?? loadAquaProfile();
  const config = runtimeConfig(profile, options.config);
  const rules = assessAquaRules(visible, context);
  const fallback: AquaAgentResult = {
    assessment: rules,
    source: rules ? 'rules' : 'quiet',
    modelName: 'Nexus evidence-bound detection',
    modelVersion: 'detection-v1',
    turns: 0,
    toolCalls: 0,
  };

  if (!config.enabled && !options.chat) return fallback;
  if (!visible.length) return fallback;

  const chat = options.chat ?? createDeskChat(config, 'AQUA');
  const state = emptyToolState();
  const tools = toolsForProfile(profile.enabledTools);
  const messages: ChatMessage[] = [
    { role: 'system', content: buildAquaSystemPrompt(profile) },
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
            modelVersion: AQUA_AGENT_VERSION,
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
        const result = executeAquaTool(
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
        if (!state.proposedAction) state.proposedAction = AQUA_ACTION_TEXT.confirm_lot_shuttle;
        const assessment = validateAquaDraft(state.draft, state, visible);
        if (!assessment) return { ...fallback, turns: turn, toolCalls };
        return {
          assessment: {
            ...assessment,
            limitations: `${assessment.limitations} AQUA agent ${modelUsed}, ${turn} turn${turn === 1 ? '' : 's'}, ${toolCalls} tool call${toolCalls === 1 ? '' : 's'}.`.trim(),
          },
          source: 'agent',
          modelName: modelUsed,
          modelVersion: AQUA_AGENT_VERSION,
          turns: turn,
          toolCalls,
        };
      }
    }
  } catch (error) {
    console.warn('[aqua-agent] Loop failed closed; using the rule assessor', {
      message: error instanceof Error ? error.message : String(error),
    });
    return fallback;
  }

  return fallback;
}

export async function assessAquaWithAgent(
  visible: DetectionEvidence[],
  context: DeskContext,
): Promise<DeskAssessment | null> {
  const result = await runAquaAgent(visible, context);
  return result.assessment;
}
