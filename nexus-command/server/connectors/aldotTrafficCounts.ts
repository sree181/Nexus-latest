import type { NormalizedObservation } from '../operational/domain.js';
import { fetchJson } from './http.js';
import { asIsoDate, canonicalHash } from './normalization.js';
import type { AuthoritativeConnector, ConnectorBatch, ConnectorContext, ConnectorDefinition } from './types.js';

const AUTHORITY_URI = 'https://aldotgis.dot.state.al.us/pubgis2/rest/services/EGISATDServices/TDMPublic/MapServer/0';

interface AldotFeature {
  attributes: Record<string, unknown>;
  geometry?: { x?: number; y?: number };
}

interface AldotResponse {
  features?: AldotFeature[];
  error?: { message?: string };
}

export class AldotTrafficCountsConnector implements AuthoritativeConnector {
  readonly definition: ConnectorDefinition = {
    code: 'aldot-traffic-counts-v1',
    sourceCode: 'aldot-traffic-counts',
    name: 'ALDOT Traffic Count Stations',
    ownerAgencyCode: 'aldot',
    ownerAgencyName: 'Alabama Department of Transportation',
    sourceType: 'api',
    authority: 'Alabama Department of Transportation',
    authorityUri: AUTHORITY_URI,
    schemaVersion: 'aldot-tdm-traffic-counter-point-v1',
    expectedCadenceSeconds: 86_400,
    staleAfterSeconds: 31_536_000,
    dataClassification: 'reference',
    permittedUse: 'Traffic-count reference and corridor context; not live speed or incident status.',
    partnerApprovalRequired: false,
    defaultConnectionStatus: 'connected',
    requiredEnvironment: [],
  };

  isConfigured(): boolean { return true; }

  async fetch(context: ConnectorContext): Promise<ConnectorBatch> {
    const params = new URLSearchParams({
      where: "IsActive = 1 AND Location LIKE '%Auburn%'",
      outFields: 'TrafficCounterDetailID,RouteID,Station,YearAADT,AADT,AverageCount,Location,Latitude,Longitude,UpdatedOnDate,LastLocationUpdateDate,IsActive,TotalNumLanes',
      returnGeometry: 'true',
      outSR: '4326',
      f: 'json',
    });
    const payload = await fetchJson<AldotResponse>(`${AUTHORITY_URI}/query?${params}`, { signal: context.signal });
    if (payload.error) throw new Error(payload.error.message ?? 'ALDOT query failed');
    const fetchedAt = new Date().toISOString();
    const observations: NormalizedObservation[] = (payload.features ?? []).map(feature => {
      const attributes = feature.attributes;
      const station = String(attributes.Station ?? attributes.TrafficCounterDetailID ?? 'unknown');
      const year = String(attributes.YearAADT ?? 'unknown');
      const latitude = Number(attributes.Latitude ?? feature.geometry?.y);
      const longitude = Number(attributes.Longitude ?? feature.geometry?.x);
      const updateValue = Number(attributes.UpdatedOnDate) > 0
        ? attributes.UpdatedOnDate
        : Number(attributes.LastLocationUpdateDate) > 0
          ? attributes.LastLocationUpdateDate
          : fetchedAt;
      const observedAt = asIsoDate(updateValue, new Date(fetchedAt));
      const normalized = {
        station,
        routeId: attributes.RouteID ?? null,
        yearAadt: attributes.YearAADT ?? null,
        aadt: attributes.AADT ?? null,
        averageCount: attributes.AverageCount ?? null,
        location: attributes.Location ?? null,
        totalLanes: attributes.TotalNumLanes ?? null,
        latitude: Number.isFinite(latitude) ? latitude : null,
        longitude: Number.isFinite(longitude) ? longitude : null,
      };
      return {
        sourceEventId: `station:${station}:${year}`,
        observedAt,
        summary: `${attributes.RouteID ?? 'ALDOT route'} station ${station}: ${attributes.AADT ?? 'unavailable'} AADT (${year})`,
        geometryGeojson: Number.isFinite(latitude) && Number.isFinite(longitude)
          ? { type: 'Point', coordinates: [longitude, latitude] }
          : null,
        attributes: normalized,
        qualityFlags: ['reference_data', 'not_live_traffic'],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: AUTHORITY_URI,
          sourceRecordUri: `${AUTHORITY_URI}/query?where=TrafficCounterDetailID%3D${attributes.TrafficCounterDetailID}&f=pjson`,
          connectorCode: this.definition.code,
          schemaVersion: this.definition.schemaVersion,
          fetchedAt,
          upstreamObservedAt: observedAt,
          termsNote: this.definition.permittedUse,
        },
      };
    });
    return {
      observations,
      upstreamObservedAt: fetchedAt,
      checkpoint: { fetchedAt, recordCount: observations.length },
      metadata: { officialLocationFilter: 'Auburn/Opelika' },
    };
  }
}
