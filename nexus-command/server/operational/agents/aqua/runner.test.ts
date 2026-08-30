import { describe, expect, it } from 'vitest';
import type { DetectionEvidence, DetectionMatch } from '../../detection/rules.js';
import { atlasAiConfig } from '../atlas/config.js';
import type { ChatCompletion, ChatFn } from '../llmClient.js';
import { AQUA_ACTION_TEXT } from './actions.js';
import { defaultAquaProfile } from './catalog.js';
import { buildAquaSystemPrompt, runAquaAgent } from './runner.js';

const EVIDENCE_ID = '00000000-0000-4000-8000-0000000000ee';

function evidence(): DetectionEvidence {
  return {
    evidenceId: EVIDENCE_ID,
    connectorCode: 'auburn-eta-spot-v1',
    sourceEventId: 'veh:ee',
    sourceName: 'Tiger Transit',
    summary: 'Two shuttles are available for event staging.',
    observedAt: new Date().toISOString(),
    contentHash: 'hash-ee',
    geometryGeojson: null,
    attributes: { availableUnits: 2, currentDelayMinutes: 4 },
  };
}

function match(): DetectionMatch {
  const primary = evidence();
  return {
    rule: {
      packCode: 'sec_gameday',
      ruleCode: 'remote-lot-staging',
      connectorCode: 'auburn-eta-spot-v1',
      agentCode: 'aqua',
      name: 'Remote-lot staging',
      whyItMatters: 'Shuttle and lot state must be confirmed.',
      severity: 'medium',
      affectedServices: ['parking', 'transit'],
      constraints: [],
      playbook: {
        recommendedAction: 'Hold the remote-lot change until Parking and Transit confirm lot and shuttle state.',
        expectedEffect: 'Named agencies acknowledge.',
        limitations: 'Shuttle positions only.',
        approvals: [],
        commitments: [],
      },
    },
    externalKey: 'tt:ee',
    title: 'Remote-lot staging under review',
    whatChanged: 'Shuttles are visible; occupancy is not connected.',
    severity: 'medium',
    primary,
    evidence: [primary],
    evidenceHash: 'snap-ee',
  };
}

function context() {
  return { match: match(), snapshot: [evidence()], liveConnectors: ['auburn-eta-spot-v1'], now: Date.now() };
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

describe('AQUA agent runner', () => {
  it('puts role, backstory, and locked rules in the system prompt', () => {
    const prompt = buildAquaSystemPrompt(defaultAquaProfile());
    expect(prompt).toContain(defaultAquaProfile().role);
    expect(prompt).toContain(defaultAquaProfile().backstory);
    expect(prompt).toMatch(/cannot change an operator schedule/i);
    expect(prompt).toMatch(/Never cite a policy id as evidence/i);
  });

  it('uses the rule assessor when no chat is configured', async () => {
    const result = await runAquaAgent([evidence()], context(), {
      config: { ...atlasAiConfig(), enabled: false },
    });
    expect(result.source).toBe('rules');
    expect(result.assessment?.citedEvidenceIds).toEqual([EVIDENCE_ID]);
  });

  it('accepts a tool loop that lists, proposes, and drafts', async () => {
    const result = await runAquaAgent([evidence()], context(), {
      config: { ...atlasAiConfig(), enabled: true, apiKey: 'test', maxTurns: 6, timeoutMs: 5_000, baseUrl: 'http://aqua.test', model: 'test-model' },
      chat: scripted([
        call('1', 'list_aqua_evidence', {}),
        call('2', 'propose_action', { family: 'hold_for_occupancy' }),
        call('3', 'draft_finding', {
          observation: 'Two Tiger Transit shuttles are visible and lot occupancy is not connected.',
          interpretation: 'A remote-lot change would treat occupancy as known when only shuttle movement is visible.',
          confidence: 0.34,
          citedEvidenceIds: [EVIDENCE_ID],
        }),
      ]),
    });
    expect(result.source).toBe('agent');
    expect(result.modelName).toBe('test-model');
    expect(result.assessment?.candidateAction).toBe(AQUA_ACTION_TEXT.hold_for_occupancy);
    expect(result.assessment?.citedEvidenceIds).toEqual([EVIDENCE_ID]);
    expect(result.toolCalls).toBe(3);
  });

  it('falls back to the rule assessor when the draft cites unread evidence', async () => {
    const result = await runAquaAgent([evidence()], context(), {
      config: { ...atlasAiConfig(), enabled: true, apiKey: 'test', maxTurns: 4, timeoutMs: 5_000, baseUrl: 'http://aqua.test', model: 'test-model' },
      chat: scripted([
        call('1', 'draft_finding', {
          observation: 'Invented occupancy on a lot AQUA never opened.',
          interpretation: 'This should be rejected because the id was never read.',
          confidence: 0.5,
          citedEvidenceIds: [EVIDENCE_ID],
        }),
      ]),
    });
    expect(result.source).toBe('rules');
    expect(result.assessment?.citedEvidenceIds).toEqual([EVIDENCE_ID]);
  });

  it('stays quiet on HEARTBEAT_OK', async () => {
    const result = await runAquaAgent([evidence()], context(), {
      config: { ...atlasAiConfig(), enabled: true, apiKey: 'test', maxTurns: 2, timeoutMs: 5_000, baseUrl: 'http://aqua.test', model: 'test-model' },
      chat: async () => ({ content: 'HEARTBEAT_OK', toolCalls: [], model: 'test-model' }),
    });
    expect(result.source).toBe('quiet');
    expect(result.assessment).toBeNull();
  });
});
