import type { NormalizedObservation } from '../operational/domain.js';
import { fetchJson } from './http.js';
import { asIsoDate, canonicalHash } from './normalization.js';
import type { AuthoritativeConnector, ConnectorBatch, ConnectorContext, ConnectorDefinition } from './types.js';

const AUTHORITY_URI = 'https://algotraffic.com/map';
const API_BASE = 'https://api.algotraffic.com';

/** Game Day operating box: Auburn / Opelika / I-85 Exit 51 corridor. */
export const AUBURN_BBOX = { south: 32.48, north: 32.72, west: -85.62, east: -85.32 };

interface AlgoLocation {
  latitude?: number;
  longitude?: number;
  city?: string | null;
  county?: string | null;
  displayRouteDesignator?: string | null;
  routeDesignator?: string | null;
  direction?: string | null;
}

interface AlgoTrafficEvent {
  id: number;
  type?: string;
  title?: string;
  subTitle?: string;
  shortSubTitle?: string;
  description?: string;
  severity?: string;
  active?: boolean;
  start?: string;
  end?: string | null;
  lastUpdatedAt?: string;
  permLink?: string | null;
  startLocation?: AlgoLocation | null;
  endLocation?: AlgoLocation | null;
}

interface AlgoTravelTime {
  id: number;
  name?: string;
  averageSpeedMph?: number;
  estimatedTimeMinutes?: number;
  congestionLevel?: string;
  lastUpdated?: string;
  totalDistanceMiles?: number;
  origin?: { name?: string; city?: string | null };
  destination?: { name?: string; city?: string | null };
}

interface AlgoMessageSign {
  id: number;
  location?: AlgoLocation | null;
  pages?: Array<{ lines?: Array<{ text?: string | null }> }>;
}

export function inAuburnOperatingBox(location: AlgoLocation | null | undefined): boolean {
  if (!location) return false;
  const city = String(location.city ?? '').toLowerCase();
  const county = String(location.county ?? '').toLowerCase();
  if (city === 'auburn' || city === 'opelika') return true;
  if (county === 'lee') return true;
  const lat = Number(location.latitude);
  const lon = Number(location.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false;
  return lat >= AUBURN_BBOX.south && lat <= AUBURN_BBOX.north && lon >= AUBURN_BBOX.west && lon <= AUBURN_BBOX.east;
}

export function isAuburnTravelTime(item: AlgoTravelTime): boolean {
  const haystack = `${item.name ?? ''} ${item.origin?.name ?? ''} ${item.destination?.name ?? ''}`.toLowerCase();
  return haystack.includes('auburn') || haystack.includes('exit 51') || haystack.includes('opelika');
}

function point(location: AlgoLocation | null | undefined): Record<string, unknown> | null {
  if (!location || !Number.isFinite(Number(location.latitude)) || !Number.isFinite(Number(location.longitude))) return null;
  return { type: 'Point', coordinates: [Number(location.longitude), Number(location.latitude)] };
}

export class AlgoTrafficConnector implements AuthoritativeConnector {
  readonly definition: ConnectorDefinition = {
    code: 'aldot-algo-traffic-v1',
    sourceCode: 'aldot-algo-traffic',
    name: 'ALGO Traffic (ALDOT traveler map)',
    ownerAgencyCode: 'aldot',
    ownerAgencyName: 'Alabama Department of Transportation',
    sourceType: 'api',
    authority: 'ALDOT / ALEA ALGO Traffic traveler information',
    authorityUri: AUTHORITY_URI,
    schemaVersion: 'algo-traveler-v4',
    expectedCadenceSeconds: 60,
    staleAfterSeconds: 300,
    dataClassification: 'live',
    permittedUse: 'Public traveler events, I-85 travel times, and Auburn message-sign text from the official ALGO map JSON. No camera imagery and no ALEA missing-person alerts.',
    partnerApprovalRequired: false,
    defaultConnectionStatus: 'connected',
    requiredEnvironment: [],
  };

  isConfigured(): boolean {
    return process.env.ALGO_TRAVELER_FEED !== 'false';
  }

  async fetch(context: ConnectorContext): Promise<ConnectorBatch> {
    const fetchedAt = new Date().toISOString();
    const [events, travelTimes, signs] = await Promise.all([
      fetchJson<AlgoTrafficEvent[]>(`${API_BASE}/v4.0/TrafficEvents`, { signal: context.signal }),
      fetchJson<AlgoTravelTime[]>(`${API_BASE}/v4.0/TravelTimes`, { signal: context.signal }),
      fetchJson<AlgoMessageSign[]>(`${API_BASE}/v3.0/MessageSigns`, { signal: context.signal }),
    ]);

    const observations: NormalizedObservation[] = [];

    for (const event of events ?? []) {
      if (event.active === false) continue;
      const location = inAuburnOperatingBox(event.startLocation) ? event.startLocation : event.endLocation;
      if (!inAuburnOperatingBox(event.startLocation) && !inAuburnOperatingBox(event.endLocation)) continue;
      const observedAt = asIsoDate(event.lastUpdatedAt ?? event.start, new Date(fetchedAt));
      const route = location?.displayRouteDesignator ?? location?.routeDesignator ?? 'Unspecified route';
      const place = event.shortSubTitle || event.subTitle || route;
      const normalized = {
        layer: 'traffic_event',
        algoEventId: event.id,
        eventType: event.type ?? 'Unknown',
        severity: event.severity ?? null,
        title: event.title ?? 'ALGO traveler event',
        subtitle: place,
        description: event.description ?? '',
        route,
        city: location?.city ?? null,
        county: location?.county ?? null,
        startsAt: event.start ?? null,
        endsAt: event.end ?? null,
        latitude: location?.latitude ?? null,
        longitude: location?.longitude ?? null,
      };
      observations.push({
        sourceEventId: `event:${event.type ?? 'unknown'}:${event.id}`,
        observedAt,
        summary: `${event.type ?? 'Event'}: ${event.title ?? 'ALGO traveler event'} — ${place}`,
        geometryGeojson: point(location),
        attributes: normalized,
        qualityFlags: event.type === 'Crash' || event.type === 'Incident' ? ['algo_traveler_event', 'official_incident'] : ['algo_traveler_event', 'published_roadwork'],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: AUTHORITY_URI,
          sourceRecordUri: event.permLink || `${API_BASE}/v4.0/TrafficEvents`,
          connectorCode: this.definition.code,
          schemaVersion: this.definition.schemaVersion,
          fetchedAt,
          upstreamObservedAt: observedAt,
          termsNote: this.definition.permittedUse,
        },
      });
    }

    for (const item of travelTimes ?? []) {
      if (!isAuburnTravelTime(item)) continue;
      const observedAt = asIsoDate(item.lastUpdated, new Date(fetchedAt));
      const normalized = {
        layer: 'travel_time',
        algoTravelTimeId: item.id,
        name: item.name ?? 'I-85 Auburn travel time',
        origin: item.origin?.name ?? null,
        destination: item.destination?.name ?? null,
        averageSpeedMph: item.averageSpeedMph ?? null,
        estimatedTimeMinutes: item.estimatedTimeMinutes ?? null,
        congestionLevel: item.congestionLevel ?? null,
        totalDistanceMiles: item.totalDistanceMiles ?? null,
      };
      const congested = Boolean(item.congestionLevel && item.congestionLevel !== 'Unaffected');
      observations.push({
        sourceEventId: `travel-time:${item.id}`,
        observedAt,
        summary: `${item.name ?? 'I-85 Auburn'}: ${item.averageSpeedMph ?? 'unknown'} mph, ${item.estimatedTimeMinutes ?? 'unknown'} min (${item.congestionLevel ?? 'unknown'})`,
        geometryGeojson: { type: 'Point', coordinates: [-85.5209, 32.5558] },
        attributes: normalized,
        qualityFlags: congested ? ['algo_travel_time', 'congestion_reported'] : ['algo_travel_time'],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: AUTHORITY_URI,
          sourceRecordUri: `${API_BASE}/v4.0/TravelTimes`,
          connectorCode: this.definition.code,
          schemaVersion: this.definition.schemaVersion,
          fetchedAt,
          upstreamObservedAt: observedAt,
          termsNote: this.definition.permittedUse,
        },
      });
    }

    for (const sign of signs ?? []) {
      if (!inAuburnOperatingBox(sign.location)) continue;
      const pages = (sign.pages ?? []).map(page => (page.lines ?? []).map(line => line.text ?? '').filter(Boolean).join(' / ')).filter(Boolean);
      const observedAt = fetchedAt;
      const normalized = {
        layer: 'message_sign',
        algoSignId: sign.id,
        route: sign.location?.displayRouteDesignator ?? null,
        direction: sign.location?.direction ?? null,
        city: sign.location?.city ?? null,
        pages,
        latitude: sign.location?.latitude ?? null,
        longitude: sign.location?.longitude ?? null,
      };
      observations.push({
        sourceEventId: `sign:${sign.id}`,
        observedAt,
        summary: `I-85 message sign ${sign.location?.direction ?? ''} ${sign.location?.city ?? 'Auburn'}: ${pages[0] || 'no text'}`.replace(/\s+/g, ' ').trim(),
        geometryGeojson: point(sign.location),
        attributes: normalized,
        qualityFlags: ['algo_message_sign'],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: AUTHORITY_URI,
          sourceRecordUri: `${API_BASE}/v3.0/MessageSigns`,
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
        eventCount: observations.filter(item => item.sourceEventId.startsWith('event:')).length,
        travelTimeCount: observations.filter(item => item.sourceEventId.startsWith('travel-time:')).length,
        signCount: observations.filter(item => item.sourceEventId.startsWith('sign:')).length,
      },
      metadata: { travelerMap: AUTHORITY_URI, excluded: ['camera_imagery', 'alea_missing_person_alerts'] },
    };
  }
}
