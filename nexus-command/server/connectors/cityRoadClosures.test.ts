import { describe, expect, it } from 'vitest';
import { isPublishedCityRecord } from './cityRoadClosures.js';

const now = Date.parse('2026-08-28T18:00:00.000Z');
const day = 24 * 60 * 60 * 1_000;

describe('isPublishedCityRecord', () => {
  it('keeps June 2026 City records that the previous same-day filter dropped', () => {
    expect(isPublishedCityRecord('2026-06-10T12:00:00.000Z', '2026-06-12T20:00:00.000Z', now)).toBe(true);
  });

  it('drops records that ended more than 120 days ago', () => {
    expect(isPublishedCityRecord('2026-01-02T00:00:00.000Z', new Date(now - 130 * day).toISOString(), now)).toBe(false);
  });

  it('drops records that start more than 45 days in the future', () => {
    expect(isPublishedCityRecord(new Date(now + 50 * day).toISOString(), new Date(now + 51 * day).toISOString(), now)).toBe(false);
  });

  it('keeps records with no structured schedule', () => {
    expect(isPublishedCityRecord(null, null, now)).toBe(true);
  });
});
