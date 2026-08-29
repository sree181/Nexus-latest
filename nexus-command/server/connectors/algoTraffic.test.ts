import { describe, expect, it } from 'vitest';
import { inAuburnOperatingBox, isAuburnTravelTime } from './algoTraffic.js';

describe('ALGO Auburn filters', () => {
  it('keeps I-85 Exit 51 / Lee County / Auburn city records', () => {
    expect(inAuburnOperatingBox({ latitude: 32.5558, longitude: -85.5209 })).toBe(true);
    expect(inAuburnOperatingBox({ city: 'Auburn', latitude: 32.54, longitude: -85.54 })).toBe(true);
    expect(inAuburnOperatingBox({ county: 'Lee', latitude: 32.72, longitude: -85.30 })).toBe(true);
  });

  it('drops distant I-85 disabled-vehicle records west of the Game Day box', () => {
    expect(inAuburnOperatingBox({ county: 'Macon', latitude: 32.4215, longitude: -85.934 })).toBe(false);
  });

  it('keeps official Auburn I-85 travel-time segments', () => {
    expect(isAuburnTravelTime({ id: 2657, name: 'Auburn to GA', origin: { name: 'I-85 N @ US-29/EXIT 51' } })).toBe(true);
    expect(isAuburnTravelTime({ id: 1, name: 'Birmingham to Tuscaloosa', origin: { name: 'I-20' } })).toBe(false);
  });
});
