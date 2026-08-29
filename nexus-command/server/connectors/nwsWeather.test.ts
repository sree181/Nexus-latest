import { describe, expect, it } from 'vitest';
import { isOperationalAlert, stableAlertKey, summarizeForecast } from './nwsWeather.js';

const NOW = Date.parse('2026-08-29T12:00:00Z');

function alert(overrides: Record<string, unknown> = {}) {
  return {
    id: 'urn:oid:2.49.0.1.840.0.abc.001.1',
    status: 'Actual',
    messageType: 'Alert',
    event: 'Tornado Watch',
    expires: '2026-08-29T18:00:00Z',
    geocode: { UGC: ['ALC081'] },
    ...overrides,
  };
}

describe('NWS alert identity', () => {
  it('keeps one key across the updates a warning receives', () => {
    const issued = alert({
      id: 'urn:oid:2.49.0.1.840.0.first.001.1',
      parameters: { VTEC: ['/O.NEW.KBMX.TO.A.0231.260829T1200Z-260829T1800Z/'] },
    });
    const updated = alert({
      id: 'urn:oid:2.49.0.1.840.0.second.001.1',
      parameters: { VTEC: ['/O.CON.KBMX.TO.A.0231.260829T1200Z-260829T1800Z/'] },
    });
    expect(stableAlertKey(issued)).toBe(stableAlertKey(updated));
    expect(stableAlertKey(issued)).toBe('KBMX.TO.A.0231.26');
  });

  it('separates two different tornado watches from the same office', () => {
    const first = alert({ parameters: { VTEC: ['/O.NEW.KBMX.TO.A.0231.260829T1200Z-260829T1800Z/'] } });
    const second = alert({ parameters: { VTEC: ['/O.NEW.KBMX.TO.A.0232.260829T1900Z-260830T0100Z/'] } });
    expect(stableAlertKey(first)).not.toBe(stableAlertKey(second));
  });

  it('falls back to the message identifier for products carrying no VTEC', () => {
    expect(stableAlertKey(alert({ event: 'Air Quality Alert' }))).toBe('urn:oid:2.49.0.1.840.0.abc.001.1');
  });
});

describe('NWS operating-area filter', () => {
  it('accepts an in-force product naming a Lee County zone', () => {
    expect(isOperationalAlert(alert(), undefined, NOW)).toBe(true);
    expect(isOperationalAlert(alert({ geocode: { UGC: ['ALZ047'] } }), undefined, NOW)).toBe(true);
  });

  it('rejects the keepalive test message the feed publishes', () => {
    expect(isOperationalAlert(alert({ status: 'Test', event: 'Test Message' }), undefined, NOW)).toBe(false);
  });

  it('rejects cancelled and expired products', () => {
    expect(isOperationalAlert(alert({ messageType: 'Cancel' }), undefined, NOW)).toBe(false);
    expect(isOperationalAlert(alert({ expires: '2026-08-29T11:00:00Z' }), undefined, NOW)).toBe(false);
  });

  it('rejects an Alabama product for a county Nexus does not operate in', () => {
    expect(isOperationalAlert(alert({ geocode: { UGC: ['ALC073'] } }), undefined, NOW)).toBe(false);
  });
});

describe('NWS forecast summary', () => {
  const periods = [
    { startTime: '2026-08-29T09:00:00-05:00', temperature: 77, temperatureUnit: 'F', probabilityOfPrecipitation: { value: 19 }, shortForecast: 'Slight Chance Showers And Thunderstorms' },
    { startTime: '2026-08-29T10:00:00-05:00', temperature: 88, temperatureUnit: 'F', probabilityOfPrecipitation: { value: 62 }, shortForecast: 'Chance Showers And Thunderstorms' },
    { startTime: '2026-08-29T11:00:00-05:00', temperature: 84, temperatureUnit: 'F', probabilityOfPrecipitation: { value: null }, shortForecast: 'Partly Sunny' },
  ];

  it('reports the peak an operator has to plan against', () => {
    const summary = summarizeForecast(periods, 3);
    expect(summary.maxTemperatureF).toBe(88);
    expect(summary.maxPrecipitationProbability).toBe(62);
    expect(summary.thunderstormHours).toBe(2);
  });

  it('holds to the requested horizon', () => {
    expect(summarizeForecast(periods, 1).hours).toHaveLength(1);
    expect(summarizeForecast([], 12).maxTemperatureF).toBeNull();
  });
});
