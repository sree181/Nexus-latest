import { describe, expect, it } from 'vitest';
import { PALETTE, WORKFLOW_AGENTS, WORKFLOW_SOURCES, sourceIsLive } from './catalog';

describe('workflow catalog', () => {
  it('routes every declared connector through a named source box', () => {
    const ids = new Set(WORKFLOW_SOURCES.map(item => item.id));
    for (const agent of WORKFLOW_AGENTS) {
      expect(agent.connectors.length).toBeGreaterThan(0);
      for (const connector of agent.connectors) {
        expect(ids.has(connector), `${agent.label} reads unknown ${connector}`).toBe(true);
      }
    }
  });

  it('keeps a palette that can drop sources, agents, stakeholder, and decision', () => {
    expect(PALETTE.some(item => item.kind === 'source')).toBe(true);
    expect(PALETTE.some(item => item.kind === 'agent')).toBe(true);
    expect(PALETTE.some(item => item.kind === 'stakeholder')).toBe(true);
    expect(PALETTE.some(item => item.kind === 'decision')).toBe(true);
  });

  it('marks a source live from a feed haystack without inventing a match', () => {
    const tomtom = WORKFLOW_SOURCES.find(item => item.id === 'tomtom-traffic-flow-v1')!;
    expect(sourceIsLive(tomtom, 'TomTom Traffic Flow tomtom-traffic-flow')).toBe(true);
    expect(sourceIsLive(tomtom, 'Parking occupancy')).toBe(false);
  });
});
