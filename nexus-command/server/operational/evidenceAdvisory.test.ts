import { describe, expect, it } from 'vitest';
import { composeLiveAdvisory, representativePoint } from './evidenceAdvisory.js';

describe('composeLiveAdvisory', () => {
  it('opens a City-bound recommendation from published closure evidence', () => {
    const advisory = composeLiveAdvisory([
      {
        evidence_id: '11111111-1111-4111-8111-111111111111',
        source_name: 'City of Auburn Road Closures',
        summary: 'Closure: Donahue Drive — Game Day lane restriction',
        geometry_geojson: { type: 'LineString', coordinates: [[-85.49, 32.60], [-85.50, 32.61]] },
        connector_code: 'coa-road-closures-v1',
      },
      {
        evidence_id: '22222222-2222-4222-8222-222222222222',
        source_name: 'Tiger Transit ETA Spot',
        summary: 'Tiger Transit vehicle 12 on Game Day shuttle',
        geometry_geojson: { type: 'Point', coordinates: [-85.48, 32.60] },
        connector_code: 'auburn-eta-spot-v1',
      },
    ]);

    expect(advisory.cityCount).toBe(1);
    expect(advisory.transitCount).toBe(1);
    expect(advisory.title).toContain('City-published');
    expect(advisory.whatChanged).toContain('Donahue Drive');
    expect(advisory.recommendedAction).toContain('Do not change signal timing');
    expect(advisory.geometry).toEqual({ type: 'Point', coordinates: [-85.50, 32.61] });
    expect(advisory.evidenceIds).toEqual(['11111111-1111-4111-8111-111111111111']);
  });
});

describe('representativePoint', () => {
  it('returns point geometry unchanged', () => {
    expect(representativePoint({ type: 'Point', coordinates: [-85.5, 32.6] }))
      .toEqual({ type: 'Point', coordinates: [-85.5, 32.6] });
  });
});
