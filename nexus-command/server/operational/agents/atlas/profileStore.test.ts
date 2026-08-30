import { afterEach, describe, expect, it } from 'vitest';
import { defaultAtlasProfile } from './catalog.js';
import { loadAtlasProfile, replaceAtlasProfileForTest, resetAtlasProfile, saveAtlasProfile } from './profileStore.js';

afterEach(() => {
  replaceAtlasProfileForTest(null);
});

describe('ATLAS profile store', () => {
  it('starts from the default role, backstory, tools, and policy notes', () => {
    const profile = loadAtlasProfile();
    expect(profile.deskCode).toBe('atlas');
    expect(profile.role).toMatch(/traffic/i);
    expect(profile.backstory.length).toBeGreaterThan(40);
    expect(profile.enabledTools).toContain('draft_finding');
    expect(profile.enabledTools).toContain('search_policies');
    expect(profile.policies.some(item => item.jurisdiction === 'state')).toBe(true);
  });

  it('keeps required tools even if the operator clears them', () => {
    const saved = saveAtlasProfile({
      role: defaultAtlasProfile().role,
      backstory: defaultAtlasProfile().backstory,
      instructions: defaultAtlasProfile().instructions,
      llm: defaultAtlasProfile().llm,
      enabledTools: ['search_policies'],
      policies: defaultAtlasProfile().policies,
    });
    expect(saved.enabledTools).toEqual(expect.arrayContaining(['list_atlas_evidence', 'propose_action', 'draft_finding']));
    expect(saved.enabledTools).not.toContain('shell');
  });

  it('rejects an unknown tool name', () => {
    expect(() => saveAtlasProfile({
      role: defaultAtlasProfile().role,
      backstory: defaultAtlasProfile().backstory,
      instructions: defaultAtlasProfile().instructions,
      llm: defaultAtlasProfile().llm,
      enabledTools: ['list_atlas_evidence', 'draft_finding', 'propose_action', 'run_shell'],
      policies: [],
    })).toThrow();
  });

  it('restores defaults', () => {
    saveAtlasProfile({
      ...defaultAtlasProfile(),
      role: 'Temporary role for a unit test of ATLAS.',
      policies: [],
    });
    expect(loadAtlasProfile().role).toMatch(/Temporary/);
    expect(resetAtlasProfile().role).toBe(defaultAtlasProfile().role);
  });
});
