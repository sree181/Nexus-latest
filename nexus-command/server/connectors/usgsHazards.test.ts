import { describe, expect, it } from 'vitest';
import { collectSiteReadings, computeRise, distanceKm } from './usgsHazards.js';

const HOUR = 60 * 60 * 1_000;
const BASE = Date.parse('2026-08-29T12:00:00Z');

describe('stream stage change', () => {
  it('measures the rise against the oldest reading inside the window', () => {
    const points = [
      { at: BASE - 3 * HOUR, value: 1.5 },
      { at: BASE - 1 * HOUR, value: 2.4 },
      { at: BASE, value: 4.1 },
    ];
    expect(computeRise(points, 3)).toBe(2.6);
  });

  it('ignores readings older than the window', () => {
    const points = [
      { at: BASE - 9 * HOUR, value: 0.5 },
      { at: BASE - 2 * HOUR, value: 3.9 },
      { at: BASE, value: 4.1 },
    ];
    expect(computeRise(points, 3)).toBe(0.2);
  });

  it('reports a fall as a negative change and refuses a single reading', () => {
    expect(computeRise([{ at: BASE - HOUR, value: 2 }, { at: BASE, value: 1.73 }], 3)).toBe(-0.27);
    expect(computeRise([{ at: BASE, value: 2 }], 3)).toBeNull();
  });
});

describe('gauge reading assembly', () => {
  function series(parameter: string, values: Array<[string, string]>) {
    return {
      sourceInfo: {
        siteName: 'CHEWACLA CREEK AT CHEWACLA STATE PARK NR AUBURN',
        siteCode: [{ value: '02418760' }],
        geoLocation: { geogLocation: { latitude: 32.55, longitude: -85.48 } },
      },
      variable: { variableCode: [{ value: parameter }] },
      values: [{ value: values.map(([dateTime, value]) => ({ dateTime, value })) }],
    };
  }

  it('merges stage and discharge reported as separate series for one site', () => {
    const sites = collectSiteReadings([
      series('00065', [['2026-08-29T08:00:00Z', '1.60'], ['2026-08-29T11:00:00Z', '1.82']]),
      series('00060', [['2026-08-29T11:00:00Z', '13.8']]),
    ]);
    expect(sites).toHaveLength(1);
    expect(sites[0]).toMatchObject({ siteCode: '02418760', gageHeightFt: 1.82, dischargeCfs: 13.8, riseFt: 0.22 });
  });

  it('drops the sentinel USGS sends when a sensor reports nothing', () => {
    const sites = collectSiteReadings([series('00065', [['2026-08-29T11:00:00Z', '-999999']])]);
    expect(sites).toHaveLength(0);
  });
});

describe('regional distance', () => {
  it('measures from Jordan-Hare so a rule can bound relevance', () => {
    expect(distanceKm(32.6025, -85.49)).toBe(0);
    expect(distanceKm(34.55, -85.18)).toBeGreaterThan(200);
  });
});
