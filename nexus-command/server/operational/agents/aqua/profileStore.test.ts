import { afterEach, describe, expect, it } from 'vitest';
import { defaultAquaProfile } from './catalog.js';
import { loadAquaProfile, replaceAquaProfileForTest, resetAquaProfile, saveAquaProfile } from './profileStore.js';

afterEach(() => {
  replaceAquaProfileForTest(null);
});

describe('AQUA profile store', () => {
  it('starts from the default role, backstory, tools, and policy notes', () => {
    const profile = loadAquaProfile();
    expect(profile.deskCode).toBe('aqua');
    expect(profile.role).toMatch(/parking|transit/i);
    expect(profile.backstory.length).toBeGreaterThan(40);
    expect(profile.enabledTools).toContain('draft_finding');
    expect(profile.enabledTools).toContain('search_policies');
    expect(profile.policies.some(item => item.jurisdiction === 'city')).toBe(true);
  });

  it('keeps required tools even if the operator clears them', () => {
    const saved = saveAquaProfile({
      role: defaultAquaProfile().role,
      backstory: defaultAquaProfile().backstory,
      instructions: defaultAquaProfile().instructions,
      llm: defaultAquaProfile().llm,
      enabledTools: ['search_policies'],
      policies: defaultAquaProfile().policies,
    });
    expect(saved.enabledTools).toEqual(expect.arrayContaining(['list_aqua_evidence', 'propose_action', 'draft_finding']));
    expect(saved.enabledTools).not.toContain('shell');
  });

  it('rejects an unknown tool name', () => {
    expect(() => saveAquaProfile({
      role: defaultAquaProfile().role,
      backstory: defaultAquaProfile().backstory,
      instructions: defaultAquaProfile().instructions,
      llm: defaultAquaProfile().llm,
      enabledTools: ['list_aqua_evidence', 'draft_finding', 'propose_action', 'run_shell'],
      policies: [],
    })).toThrow();
  });

  it('restores defaults', () => {
    saveAquaProfile({
      ...defaultAquaProfile(),
      role: 'Temporary role for a unit test of AQUA.',
      policies: [],
    });
    expect(loadAquaProfile().role).toMatch(/Temporary/);
    expect(resetAquaProfile().role).toBe(defaultAquaProfile().role);
  });
});
