import { describe, expect, it } from 'vitest';
import type { DetectionEvidence, DetectionMatch } from '../../detection/rules.js';
import { ATLAS_ACTION_TEXT } from './actions.js';
import type { ChatCompletion, ChatFn } from './client.js';
import { defaultAtlasProfile } from './catalog.js';
import { atlasAiConfig } from './config.js';
import { buildAtlasSystemPrompt, runAtlasAgent } from './runner.js';

const EVIDENCE_ID = '00000000-0000-4000-8000-0000000000cc';

function evidence(): DetectionEvidence {
  return {
    evidenceId: EVIDENCE_ID,
    connectorCode: 'aldot-algo-traffic-v1',
    sourceEventId: 'event:cc',
    sourceName: 'ALGO',
    summary: 'I-85 southbound at Exit 51 heavy congestion',
    observedAt: new Date().toISOString(),
    contentHash: 'hash-cc',
    geometryGeojson: null,
    attributes: { layer: 'travel_time', congestionLevel: 'Heavy', currentSpeedMph: 24, freeFlowSpeedMph: 68 },
  };
}

function match(): DetectionMatch {
  const primary = evidence();
  return {
    rule: {
      packCode: 'sec_gameday',
      ruleCode: 'algo-corridor-congestion',
      connectorCode: 'aldot-algo-traffic-v1',
      agentCode: 'atlas',
      name: 'Corridor congestion',
      whyItMatters: 'Spillback risk.',
      severity: 'medium',
      affectedServices: ['traffic'],
      constraints: [],
      playbook: {
        recommendedAction: 'Confirm the corridor picture with traffic operations.',
        expectedEffect: 'Named agencies acknowledge.',
        limitations: 'Travel time only.',
        approvals: [],
        commitments: [],
      },
    },
    externalKey: 'tt:7',
    title: 'Heavy congestion on I-85',
    whatChanged: 'ALGO reported heavy congestion.',
    severity: 'medium',
    primary,
    evidence: [primary],
    evidenceHash: 'snap-cc',
  };
}

function context() {
  return { match: match(), snapshot: [evidence()], liveConnectors: ['aldot-algo-traffic-v1'], now: Date.now() };
}

function scripted(turns: ChatCompletion[]): ChatFn {
  let index = 0;
  return async () => {
    const next = turns[index];
    index += 1;
    if (!next) return { content: 'HEARTBEAT_OK', toolCalls: [], model: 'test-model' };
    return next;
  };
}

function call(id: string, name: string, args: Record<string, unknown>): ChatCompletion {
  return {
    content: null,
    model: 'test-model',
    toolCalls: [{ id, type: 'function', function: { name, arguments: JSON.stringify(args) } }],
  };
}

describe('ATLAS agent runner', () => {
  it('puts role, backstory, and locked rules in the system prompt', () => {
    const prompt = buildAtlasSystemPrompt(defaultAtlasProfile());
    expect(prompt).toContain(defaultAtlasProfile().role);
    expect(prompt).toContain(defaultAtlasProfile().backstory);
    expect(prompt).toMatch(/cannot change a signal/i);
    expect(prompt).toMatch(/Never cite a policy id as evidence/i);
  });

  it('is off without an explicit flag and key', () => {
    expect(atlasAiConfig({}).enabled).toBe(false);
    expect(atlasAiConfig({ ATLAS_AI_ENABLED: 'true' }).enabled).toBe(false);
    expect(atlasAiConfig({ ATLAS_AI_ENABLED: 'true', GROQ_API_KEY: 'gsk_test' }).enabled).toBe(true);
    expect(atlasAiConfig({ ATLAS_AI_ENABLED: 'true', GROQ_API_KEY: 'gsk_test' }).model).toBe('openai/gpt-oss-20b');
  });

  it('uses the rule assessor when no chat is configured', async () => {
    const result = await runAtlasAgent([evidence()], context(), {
      config: { ...atlasAiConfig(), enabled: false },
    });
    expect(result.source).toBe('rules');
    expect(result.assessment?.citedEvidenceIds).toEqual([EVIDENCE_ID]);
  });

  it('accepts a tool loop that lists, proposes, and drafts', async () => {
    const result = await runAtlasAgent([evidence()], context(), {
      config: { ...atlasAiConfig(), enabled: true, apiKey: 'test', maxTurns: 6, timeoutMs: 5_000, baseUrl: 'http://atlas.test', model: 'test-model' },
      chat: scripted([
        call('1', 'list_atlas_evidence', {}),
        call('2', 'propose_action', { family: 'hold_no_change' }),
        call('3', 'draft_finding', {
          observation: 'One I-85 corridor is below free flow and reporting heavy congestion.',
          interpretation: 'A routing change would land on an approach that already has little headroom.',
          confidence: 0.66,
          citedEvidenceIds: [EVIDENCE_ID],
        }),
      ]),
    });
    expect(result.source).toBe('agent');
    expect(result.modelName).toBe('test-model');
    expect(result.assessment?.candidateAction).toBe(ATLAS_ACTION_TEXT.hold_no_change);
    expect(result.assessment?.citedEvidenceIds).toEqual([EVIDENCE_ID]);
    expect(result.toolCalls).toBe(3);
  });

  it('falls back to the rule assessor when the draft cites unread evidence', async () => {
    const result = await runAtlasAgent([evidence()], context(), {
      config: { ...atlasAiConfig(), enabled: true, apiKey: 'test', maxTurns: 4, timeoutMs: 5_000, baseUrl: 'http://atlas.test', model: 'test-model' },
      chat: scripted([
        call('1', 'draft_finding', {
          observation: 'Invented congestion on a corridor ATLAS never opened.',
          interpretation: 'This should be rejected because the id was never read.',
          confidence: 0.7,
          citedEvidenceIds: [EVIDENCE_ID],
        }),
      ]),
    });
    expect(result.source).toBe('rules');
    expect(result.assessment?.citedEvidenceIds).toEqual([EVIDENCE_ID]);
  });

  it('stays quiet on HEARTBEAT_OK', async () => {
    const result = await runAtlasAgent([evidence()], context(), {
      config: { ...atlasAiConfig(), enabled: true, apiKey: 'test', maxTurns: 2, timeoutMs: 5_000, baseUrl: 'http://atlas.test', model: 'test-model' },
      chat: async () => ({ content: 'HEARTBEAT_OK', toolCalls: [], model: 'test-model' }),
    });
    expect(result.source).toBe('quiet');
    expect(result.assessment).toBeNull();
  });
});
