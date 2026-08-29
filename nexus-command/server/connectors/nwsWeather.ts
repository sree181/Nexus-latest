import type { NormalizedObservation } from '../operational/domain.js';
import { fetchJson } from './http.js';
import { asFiniteNumber, asIsoDate, canonicalHash } from './normalization.js';
import type { AuthoritativeConnector, ConnectorBatch, ConnectorContext, ConnectorDefinition } from './types.js';

const AUTHORITY_URI = 'https://api.weather.gov';

/**
 * Lee County public zones. `ALC081` is the county zone carried by warnings; `ALZ047` is the
 * forecast zone carried by watches and advisories. An alert must name one of these to be
 * treated as affecting the operating area.
 */
export const AUBURN_WEATHER_ZONES = ['ALC081', 'ALZ047'];

/** NWS grid cell covering Jordan-Hare Stadium, resolved from api.weather.gov/points. */
const FORECAST_GRID = { office: 'BMX', x: 111, y: 47 };

/** How much of the hourly grid the command center carries as operating context. */
export const FORECAST_HOURS = 12;

interface AlertProperties {
  id?: string;
  event?: string;
  headline?: string | null;
  description?: string | null;
  instruction?: string | null;
  severity?: string;
  urgency?: string;
  certainty?: string;
  status?: string;
  messageType?: string;
  onset?: string | null;
  effective?: string | null;
  expires?: string | null;
  ends?: string | null;
  sent?: string | null;
  areaDesc?: string;
  senderName?: string;
  geocode?: { UGC?: string[]; SAME?: string[] };
  parameters?: Record<string, unknown>;
}

interface AlertFeature {
  geometry?: Record<string, unknown> | null;
  properties?: AlertProperties;
}

interface ForecastPeriod {
  number?: number;
  startTime?: string;
  endTime?: string;
  temperature?: number;
  temperatureUnit?: string;
  probabilityOfPrecipitation?: { value?: number | null } | null;
  windSpeed?: string;
  windDirection?: string;
  shortForecast?: string;
}

/**
 * A warning keeps one identity across every update it receives. The alert `id` is a digest of
 * the message, so it changes on each update and would open a second incident for the same
 * storm. VTEC carries the office, phenomenon, significance, and event tracking number, which
 * together stay fixed for the life of the event.
 */
export function stableAlertKey(properties: AlertProperties): string {
  const vtec = properties.parameters?.VTEC;
  const raw = Array.isArray(vtec) ? String(vtec[0] ?? '') : String(vtec ?? '');
  const match = /\/[A-Z]\.[A-Z]{3}\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.(\d{6})T/.exec(raw);
  if (match) {
    const [, office, phenomenon, significance, tracking, start] = match;
    return `${office}.${phenomenon}.${significance}.${tracking}.${start.slice(0, 2)}`;
  }
  // Statements and air-quality products carry no VTEC. They are reissued rather than updated,
  // so the message identifier is the best available key.
  return String(properties.id ?? 'unknown');
}

/** Only real, in-force products for the operating area become evidence. */
export function isOperationalAlert(
  properties: AlertProperties,
  zones: string[] = AUBURN_WEATHER_ZONES,
  now = Date.now(),
): boolean {
  if (properties.status !== 'Actual') return false;
  if (properties.messageType === 'Cancel') return false;
  const expires = properties.expires ? Date.parse(properties.expires) : NaN;
  if (Number.isFinite(expires) && expires < now) return false;
  const ugc = properties.geocode?.UGC ?? [];
  return ugc.some(zone => zones.includes(zone));
}

/** Condenses the hourly grid into the horizon an operator can actually act on. */
export function summarizeForecast(periods: ForecastPeriod[], hours = FORECAST_HOURS): {
  hours: Array<Record<string, unknown>>;
  maxTemperatureF: number | null;
  maxPrecipitationProbability: number | null;
  thunderstormHours: number;
} {
  const window = periods.slice(0, hours).map(period => ({
    startTime: period.startTime ?? null,
    temperatureF: period.temperatureUnit === 'F' ? asFiniteNumber(period.temperature) : null,
    precipitationProbability: asFiniteNumber(period.probabilityOfPrecipitation?.value),
    windSpeed: period.windSpeed ?? null,
    windDirection: period.windDirection ?? null,
    shortForecast: period.shortForecast ?? null,
  }));
  const temperatures = window.map(item => item.temperatureF).filter((value): value is number => value !== null);
  const precipitation = window.map(item => item.precipitationProbability).filter((value): value is number => value !== null);
  return {
    hours: window,
    maxTemperatureF: temperatures.length ? Math.max(...temperatures) : null,
    maxPrecipitationProbability: precipitation.length ? Math.max(...precipitation) : null,
    thunderstormHours: window.filter(item => /thunder/i.test(String(item.shortForecast ?? ''))).length,
  };
}

export class NwsWeatherConnector implements AuthoritativeConnector {
  readonly definition: ConnectorDefinition = {
    code: 'nws-weather-alerts-v1',
    sourceCode: 'nws-weather-alerts',
    name: 'National Weather Service alerts and forecast',
    ownerAgencyCode: 'nws-birmingham',
    ownerAgencyName: 'NOAA National Weather Service, Birmingham',
    sourceType: 'api',
    authority: 'NOAA National Weather Service',
    authorityUri: AUTHORITY_URI,
    schemaVersion: 'nws-api-v1',
    expectedCadenceSeconds: 300,
    staleAfterSeconds: 1_800,
    dataClassification: 'live',
    permittedUse: 'Public NWS watches, warnings, advisories, and gridded forecast. Nexus reproduces the product and never issues, amends, or cancels a public alert.',
    partnerApprovalRequired: false,
    defaultConnectionStatus: 'connected',
    requiredEnvironment: [],
  };

  isConfigured(): boolean {
    return process.env.NWS_WEATHER_FEED !== 'false';
  }

  async fetch(context: ConnectorContext): Promise<ConnectorBatch> {
    const fetchedAt = new Date().toISOString();
    const now = Date.parse(fetchedAt);
    const [alerts, forecast] = await Promise.all([
      fetchJson<{ features?: AlertFeature[] }>(`${AUTHORITY_URI}/alerts/active?area=AL`, { signal: context.signal }),
      fetchJson<{ properties?: { periods?: ForecastPeriod[] } }>(
        `${AUTHORITY_URI}/gridpoints/${FORECAST_GRID.office}/${FORECAST_GRID.x},${FORECAST_GRID.y}/forecast/hourly`,
        { signal: context.signal },
      ),
    ]);

    const observations: NormalizedObservation[] = [];

    for (const feature of alerts.features ?? []) {
      const properties = feature.properties ?? {};
      if (!isOperationalAlert(properties, AUBURN_WEATHER_ZONES, now)) continue;
      const alertKey = stableAlertKey(properties);
      const eventName = properties.event ?? 'Weather alert';
      const observedAt = asIsoDate(properties.sent ?? properties.effective, new Date(fetchedAt));
      const normalized = {
        layer: 'weather_alert',
        alertId: alertKey,
        messageId: properties.id ?? null,
        eventName,
        headline: properties.headline ?? null,
        description: properties.description ?? null,
        instruction: properties.instruction ?? null,
        severity: properties.severity ?? 'Unknown',
        urgency: properties.urgency ?? 'Unknown',
        certainty: properties.certainty ?? 'Unknown',
        messageType: properties.messageType ?? 'Alert',
        onsetAt: properties.onset ?? properties.effective ?? null,
        expiresAt: properties.expires ?? null,
        endsAt: properties.ends ?? null,
        areaDesc: properties.areaDesc ?? null,
        senderName: properties.senderName ?? null,
        zones: properties.geocode?.UGC ?? [],
      };
      observations.push({
        sourceEventId: `alert:${alertKey}`,
        observedAt,
        summary: `${eventName}: ${properties.headline ?? properties.areaDesc ?? 'Lee County'}`,
        geometryGeojson: feature.geometry ?? null,
        attributes: normalized,
        qualityFlags: properties.severity === 'Extreme' || properties.severity === 'Severe'
          ? ['nws_alert', 'severe_or_extreme']
          : ['nws_alert'],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: AUTHORITY_URI,
          sourceRecordUri: properties.id ? `${AUTHORITY_URI}/alerts/${properties.id}` : `${AUTHORITY_URI}/alerts/active?area=AL`,
          connectorCode: this.definition.code,
          schemaVersion: this.definition.schemaVersion,
          fetchedAt,
          upstreamObservedAt: observedAt,
          termsNote: this.definition.permittedUse,
        },
      });
    }

    const summary = summarizeForecast(forecast.properties?.periods ?? []);
    if (summary.hours.length) {
      const gridCode = `${FORECAST_GRID.office}/${FORECAST_GRID.x},${FORECAST_GRID.y}`;
      const normalized = { layer: 'hourly_forecast', gridCode, ...summary };
      const first = summary.hours[0];
      observations.push({
        sourceEventId: `forecast:${gridCode}`,
        observedAt: asIsoDate(first.startTime, new Date(fetchedAt)),
        summary: `Next ${summary.hours.length}h at Jordan-Hare: ${first.shortForecast ?? 'no forecast text'}, up to ${
          summary.maxTemperatureF ?? 'unknown'
        }F, ${summary.maxPrecipitationProbability ?? 0}% precipitation`,
        geometryGeojson: { type: 'Point', coordinates: [-85.4900, 32.6025] },
        attributes: normalized,
        qualityFlags: ['nws_gridded_forecast', 'operating_context'],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: AUTHORITY_URI,
          sourceRecordUri: `${AUTHORITY_URI}/gridpoints/${gridCode}/forecast/hourly`,
          connectorCode: this.definition.code,
          schemaVersion: this.definition.schemaVersion,
          fetchedAt,
          upstreamObservedAt: asIsoDate(first.startTime, new Date(fetchedAt)),
          termsNote: this.definition.permittedUse,
        },
      });
    }

    return {
      observations,
      upstreamObservedAt: fetchedAt,
      checkpoint: {
        fetchedAt,
        alertCount: observations.filter(item => item.sourceEventId.startsWith('alert:')).length,
        forecastHours: summary.hours.length,
      },
      metadata: { zones: AUBURN_WEATHER_ZONES, grid: `${FORECAST_GRID.office}/${FORECAST_GRID.x},${FORECAST_GRID.y}` },
    };
  }
}
