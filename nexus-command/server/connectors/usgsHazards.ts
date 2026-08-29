import type { NormalizedObservation } from '../operational/domain.js';
import { fetchJson } from './http.js';
import { asFiniteNumber, asIsoDate, canonicalHash } from './normalization.js';
import type { AuthoritativeConnector, ConnectorBatch, ConnectorContext, ConnectorDefinition } from './types.js';

const AUTHORITY_URI = 'https://waterdata.usgs.gov';
const WATER_API = 'https://waterservices.usgs.gov/nwis/iv/';
const QUAKE_API = 'https://earthquake.usgs.gov/fdsnws/event/1/query';

/** Jordan-Hare Stadium, used as the distance origin for regional hazards. */
const ORIGIN = { latitude: 32.6025, longitude: -85.4900 };

/** Lee County, Alabama. Gauges are selected by county so a new gauge appears without a code change. */
const COUNTY_CODE = '01081';

/** Gage height in feet. */
const GAGE_HEIGHT = '00065';
/** Discharge in cubic feet per second. */
const DISCHARGE = '00060';

/** Regional earthquakes are only meaningful to Auburn operations within this radius. */
export const QUAKE_RADIUS_KM = 300;

/** The window over which stage change is measured. */
export const RISE_WINDOW_HOURS = 3;

/** USGS sends this in place of a reading when the sensor reports nothing. */
const NO_DATA = -999999;

interface NwisValue { value?: string; dateTime?: string; qualifiers?: string[] }

interface NwisTimeSeries {
  sourceInfo?: {
    siteName?: string;
    siteCode?: Array<{ value?: string }>;
    geoLocation?: { geogLocation?: { latitude?: number; longitude?: number } };
  };
  variable?: { variableCode?: Array<{ value?: string }>; variableName?: string; unit?: { unitCode?: string } };
  values?: Array<{ value?: NwisValue[] }>;
}

interface QuakeFeature {
  id?: string;
  geometry?: { coordinates?: number[] } | null;
  properties?: {
    mag?: number | null;
    place?: string | null;
    time?: number | null;
    updated?: number | null;
    type?: string | null;
    status?: string | null;
    tsunami?: number | null;
    title?: string | null;
    url?: string | null;
  };
}

interface SiteReading {
  siteCode: string;
  siteName: string;
  latitude: number | null;
  longitude: number | null;
  gageHeightFt: number | null;
  dischargeCfs: number | null;
  riseFt: number | null;
  observedAt: string | null;
}

function readings(series: NwisTimeSeries): Array<{ at: number; value: number }> {
  return (series.values?.[0]?.value ?? [])
    .map(point => ({ at: Date.parse(String(point.dateTime ?? '')), value: asFiniteNumber(point.value) ?? NO_DATA }))
    .filter(point => Number.isFinite(point.at) && point.value !== NO_DATA)
    .sort((left, right) => left.at - right.at);
}

/**
 * Stage change measured against the oldest reading still inside the window. Nexus has no
 * published flood stage for these creeks, so it reports the movement the gauge actually
 * recorded rather than asserting a flood threshold it was not given.
 */
export function computeRise(points: Array<{ at: number; value: number }>, windowHours = RISE_WINDOW_HOURS): number | null {
  if (points.length < 2) return null;
  const latest = points[points.length - 1];
  const cutoff = latest.at - windowHours * 60 * 60 * 1_000;
  const baseline = points.find(point => point.at >= cutoff);
  if (!baseline || baseline.at === latest.at) return null;
  return Number((latest.value - baseline.value).toFixed(2));
}

export function distanceKm(latitude: number, longitude: number, origin = ORIGIN): number {
  const toRadians = (degrees: number) => (degrees * Math.PI) / 180;
  const earthRadiusKm = 6_371;
  const deltaLat = toRadians(latitude - origin.latitude);
  const deltaLon = toRadians(longitude - origin.longitude);
  const a = Math.sin(deltaLat / 2) ** 2
    + Math.cos(toRadians(origin.latitude)) * Math.cos(toRadians(latitude)) * Math.sin(deltaLon / 2) ** 2;
  return Math.round(earthRadiusKm * 2 * Math.asin(Math.sqrt(a)));
}

export function collectSiteReadings(timeSeries: NwisTimeSeries[]): SiteReading[] {
  const sites = new Map<string, SiteReading>();
  for (const series of timeSeries) {
    const siteCode = series.sourceInfo?.siteCode?.[0]?.value;
    if (!siteCode) continue;
    const points = readings(series);
    if (!points.length) continue;
    const location = series.sourceInfo?.geoLocation?.geogLocation;
    const existing = sites.get(siteCode) ?? {
      siteCode,
      siteName: series.sourceInfo?.siteName ?? siteCode,
      latitude: asFiniteNumber(location?.latitude),
      longitude: asFiniteNumber(location?.longitude),
      gageHeightFt: null,
      dischargeCfs: null,
      riseFt: null,
      observedAt: null,
    };
    const latest = points[points.length - 1];
    const parameter = series.variable?.variableCode?.[0]?.value;
    if (parameter === GAGE_HEIGHT) {
      existing.gageHeightFt = latest.value;
      existing.riseFt = computeRise(points);
    } else if (parameter === DISCHARGE) {
      existing.dischargeCfs = latest.value;
    }
    const at = new Date(latest.at).toISOString();
    if (!existing.observedAt || at > existing.observedAt) existing.observedAt = at;
    sites.set(siteCode, existing);
  }
  return [...sites.values()];
}

export class UsgsHazardsConnector implements AuthoritativeConnector {
  readonly definition: ConnectorDefinition = {
    code: 'usgs-natural-hazards-v1',
    sourceCode: 'usgs-natural-hazards',
    name: 'USGS stream gauges and regional seismicity',
    ownerAgencyCode: 'usgs',
    ownerAgencyName: 'United States Geological Survey',
    sourceType: 'api',
    authority: 'U.S. Geological Survey',
    authorityUri: AUTHORITY_URI,
    schemaVersion: 'usgs-nwis-iv-and-fdsnws-v1',
    expectedCadenceSeconds: 900,
    staleAfterSeconds: 3_600,
    dataClassification: 'live',
    permittedUse: 'Public provisional gauge readings and reviewed earthquake catalog. Provisional readings are subject to revision and carry no flood-stage determination.',
    partnerApprovalRequired: false,
    defaultConnectionStatus: 'connected',
    requiredEnvironment: [],
  };

  isConfigured(): boolean {
    return process.env.USGS_HAZARDS_FEED !== 'false';
  }

  async fetch(context: ConnectorContext): Promise<ConnectorBatch> {
    const fetchedAt = new Date().toISOString();
    const waterParams = new URLSearchParams({
      format: 'json',
      countyCd: COUNTY_CODE,
      parameterCd: `${DISCHARGE},${GAGE_HEIGHT}`,
      period: `PT${RISE_WINDOW_HOURS * 2}H`,
      siteStatus: 'active',
    });
    const quakeParams = new URLSearchParams({
      format: 'geojson',
      latitude: String(ORIGIN.latitude),
      longitude: String(ORIGIN.longitude),
      maxradiuskm: String(QUAKE_RADIUS_KM),
      starttime: new Date(Date.parse(fetchedAt) - 7 * 24 * 60 * 60 * 1_000).toISOString().slice(0, 10),
      minmagnitude: '2.5',
      orderby: 'time',
    });

    const [water, quakes] = await Promise.all([
      fetchJson<{ value?: { timeSeries?: NwisTimeSeries[] } }>(`${WATER_API}?${waterParams}`, { signal: context.signal }),
      fetchJson<{ features?: QuakeFeature[] }>(`${QUAKE_API}?${quakeParams}`, { signal: context.signal }),
    ]);

    const observations: NormalizedObservation[] = [];

    for (const site of collectSiteReadings(water.value?.timeSeries ?? [])) {
      const observedAt = site.observedAt ?? fetchedAt;
      const normalized = {
        layer: 'stream_gauge',
        siteCode: site.siteCode,
        siteName: site.siteName,
        gageHeightFt: site.gageHeightFt,
        dischargeCfs: site.dischargeCfs,
        riseFt: site.riseFt,
        riseWindowHours: RISE_WINDOW_HOURS,
        latitude: site.latitude,
        longitude: site.longitude,
      };
      const rising = site.riseFt !== null && site.riseFt > 0;
      observations.push({
        sourceEventId: `gauge:${site.siteCode}`,
        observedAt,
        summary: `${site.siteName}: ${site.gageHeightFt ?? 'unknown'} ft stage${
          site.riseFt === null ? '' : `, ${site.riseFt >= 0 ? '+' : ''}${site.riseFt} ft over ${RISE_WINDOW_HOURS}h`
        }`,
        geometryGeojson: site.latitude !== null && site.longitude !== null
          ? { type: 'Point', coordinates: [site.longitude, site.latitude] }
          : null,
        attributes: normalized,
        qualityFlags: rising ? ['usgs_provisional', 'stage_rising'] : ['usgs_provisional'],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: AUTHORITY_URI,
          sourceRecordUri: `${AUTHORITY_URI}/monitoring-location/${site.siteCode}/`,
          connectorCode: this.definition.code,
          schemaVersion: this.definition.schemaVersion,
          fetchedAt,
          upstreamObservedAt: observedAt,
          termsNote: this.definition.permittedUse,
        },
      });
    }

    for (const quake of quakes.features ?? []) {
      const properties = quake.properties ?? {};
      if (properties.type !== 'earthquake') continue;
      const quakeId = String(quake.id ?? '');
      if (!quakeId) continue;
      const coordinates = quake.geometry?.coordinates ?? [];
      const longitude = asFiniteNumber(coordinates[0]);
      const latitude = asFiniteNumber(coordinates[1]);
      const observedAt = asIsoDate(properties.time, new Date(fetchedAt));
      const normalized = {
        layer: 'earthquake',
        quakeId,
        magnitude: asFiniteNumber(properties.mag),
        place: properties.place ?? null,
        depthKm: asFiniteNumber(coordinates[2]),
        distanceKm: latitude !== null && longitude !== null ? distanceKm(latitude, longitude) : null,
        reviewStatus: properties.status ?? null,
        occurredAt: observedAt,
        latitude,
        longitude,
      };
      observations.push({
        sourceEventId: `quake:${quakeId}`,
        observedAt,
        summary: properties.title ?? `M ${properties.mag ?? '?'} earthquake near ${properties.place ?? 'the region'}`,
        geometryGeojson: latitude !== null && longitude !== null
          ? { type: 'Point', coordinates: [longitude, latitude] }
          : null,
        attributes: normalized,
        qualityFlags: properties.status === 'reviewed' ? ['usgs_reviewed'] : ['usgs_automatic'],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: AUTHORITY_URI,
          sourceRecordUri: properties.url ?? `${QUAKE_API}?eventid=${quakeId}`,
          connectorCode: this.definition.code,
          schemaVersion: this.definition.schemaVersion,
          fetchedAt,
          upstreamObservedAt: observedAt,
          termsNote: this.definition.permittedUse,
        },
      });
    }

    return {
      observations,
      upstreamObservedAt: fetchedAt,
      checkpoint: {
        fetchedAt,
        gaugeCount: observations.filter(item => item.sourceEventId.startsWith('gauge:')).length,
        quakeCount: observations.filter(item => item.sourceEventId.startsWith('quake:')).length,
      },
      metadata: { countyCode: COUNTY_CODE, quakeRadiusKm: QUAKE_RADIUS_KM },
    };
  }
}
