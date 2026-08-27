import type { NormalizedObservation } from '../operational/domain.js';
import { fetchJson } from './http.js';
import { asIsoDate, canonicalHash } from './normalization.js';
import type { AuthoritativeConnector, ConnectorBatch, ConnectorContext, ConnectorDefinition } from './types.js';

const AUTHORITY_URI = 'https://ocean.auburnalabama.org/arcgis/rest/services/Hosted/RoadClosuresPublic/FeatureServer';
const LAYERS = [
  { id: 0, kind: 'block', fields: 'objectid,globalid,street,direction,reason,accessallowed,description,starttime,endtime,activeincid,department,effectedservices,last_edited_date' },
  { id: 1, kind: 'closure', fields: 'objectid,globalid,street,direction,reason,laneimpact,accessallowed,description,starttime,endtime,altroute,activeincid,department,effectedservices,last_edited_date' },
  { id: 2, kind: 'detour', fields: 'objectid,globalid,street,description,starttime,endtime,activeincid,department,effectedservices,last_edited_date' },
] as const;

interface GeoJsonFeature {
  id?: string | number;
  geometry?: Record<string, unknown> | null;
  properties?: Record<string, unknown>;
}
interface FeatureCollection { features?: GeoJsonFeature[]; }

function property(properties: Record<string, unknown>, names: string[]): unknown {
  for (const name of names) if (properties[name] !== undefined && properties[name] !== null) return properties[name];
  return null;
}

export class CityRoadClosuresConnector implements AuthoritativeConnector {
  readonly definition: ConnectorDefinition = {
    code: 'coa-road-closures-v1',
    sourceCode: 'coa-road-closures',
    name: 'City of Auburn Road Closures',
    ownerAgencyCode: 'city-gis',
    ownerAgencyName: 'City of Auburn GIS Division',
    sourceType: 'api',
    authority: 'City of Auburn GIS Division',
    authorityUri: AUTHORITY_URI,
    schemaVersion: 'coa-road-closures-geojson-v1',
    expectedCadenceSeconds: 60,
    staleAfterSeconds: 300,
    dataClassification: 'live',
    permittedUse: 'Public read-only blocks, closures, and detours; verify operational decisions with Event Command.',
    partnerApprovalRequired: false,
    defaultConnectionStatus: 'connected',
    requiredEnvironment: [],
  };

  isConfigured(): boolean { return true; }

  async fetch(context: ConnectorContext): Promise<ConnectorBatch> {
    const fetchedAt = new Date().toISOString();
    const collections = await Promise.all(LAYERS.map(async layer => {
      const params = new URLSearchParams({
        where: '1=1',
        outFields: layer.fields,
        returnGeometry: 'true', outSR: '4326', f: 'geojson',
      });
      const collection = await fetchJson<FeatureCollection>(`${AUTHORITY_URI}/${layer.id}/query?${params}`, { signal: context.signal });
      const now = Date.now();
      const horizon = now + 24 * 60 * 60 * 1_000;
      const features = (collection.features ?? []).filter(feature => {
        const properties = feature.properties ?? {};
        const startValue = property(properties, ['starttime', 'StartTime']);
        const endValue = property(properties, ['endtime', 'EndTime']);
        const start = startValue === null ? null : new Date(asIsoDate(startValue)).getTime();
        const end = endValue === null ? null : new Date(asIsoDate(endValue)).getTime();
        return (start === null || start <= horizon) && (end === null || end >= now);
      });
      return { ...layer, features };
    }));
    const observations: NormalizedObservation[] = collections.flatMap(layer => layer.features.map((feature, index) => {
      const properties = feature.properties ?? {};
      const recordId = String(property(properties, ['GlobalID', 'globalid', 'OBJECTID', 'objectid']) ?? feature.id ?? index);
      const road = String(property(properties, ['street', 'StreetName', 'RoadName', 'FullStreetName', 'Name', 'Location']) ?? 'Unnamed road');
      const description = String(property(properties, ['description', 'Description', 'ClosureDescription', 'reason', 'Reason', 'Notes']) ?? 'No public description');
      const observedAt = asIsoDate(property(properties, ['EditDate', 'last_edited_date', 'UpdatedAt', 'StartDate']), new Date());
      const startsAt = property(properties, ['starttime', 'StartTime']);
      const endsAt = property(properties, ['endtime', 'EndTime']);
      const normalized = { layerId: layer.id, kind: layer.kind, road, description, startsAt, properties };
      return {
        sourceEventId: `${layer.kind}:${recordId}`,
        observedAt,
        summary: `${layer.kind[0].toUpperCase()}${layer.kind.slice(1)}: ${road} — ${description}`,
        geometryGeojson: feature.geometry ?? null,
        attributes: normalized,
        qualityFlags: startsAt === null && endsAt === null
          ? ['active_public_record', 'schedule_not_structured']
          : startsAt !== null && new Date(asIsoDate(startsAt)).getTime() > Date.now()
            ? ['scheduled_within_24_hours']
            : ['active_public_record'],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: AUTHORITY_URI,
          sourceRecordUri: `${AUTHORITY_URI}/${layer.id}/${recordId}`,
          connectorCode: this.definition.code,
          schemaVersion: this.definition.schemaVersion,
          fetchedAt,
          upstreamObservedAt: observedAt,
          termsNote: this.definition.permittedUse,
        },
      };
    }));
    return {
      observations,
      upstreamObservedAt: fetchedAt,
      checkpoint: { fetchedAt, recordsByLayer: Object.fromEntries(collections.map(item => [item.kind, item.features.length])) },
      metadata: { featureService: AUTHORITY_URI },
    };
  }
}
