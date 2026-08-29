import { fetchJson } from '../connectors/http.js';
import { ConnectorError } from '../connectors/types.js';

/**
 * City of Auburn published asset geometry. These layers describe what is permanently in the
 * ground, not what is happening right now, so they are served as map reference and never
 * ingested as evidence. Nothing here can open an incident.
 */

const MAP_SERVER = 'https://gisportal.auburnalabama.org/server/rest/services/Transportation/Transportation/MapServer';

/** Matches the operating extent the command-center map is bounded to. */
const EXTENT = { west: -85.57, south: 32.55, east: -85.39, north: 32.67 };

/** City GIS asset inventories change on a work-order cadence, not an operational one. */
const CACHE_TTL_MS = 12 * 60 * 60 * 1_000;

export interface ReferenceLayerDefinition {
  code: string;
  name: string;
  description: string;
  geometryType: 'point' | 'polygon';
  layerId: number;
  fields: string[];
  /** What the layer does not tell an operator, stated where they will read it. */
  limitations: string;
}

export const cityReferenceLayers: ReferenceLayerDefinition[] = [
  {
    code: 'traffic-signals',
    name: 'Traffic signals',
    description: 'City-maintained signalised intersections.',
    geometryType: 'point',
    layerId: 10,
    fields: ['OBJECTID', 'FacilityID', 'LifecycleStatus', 'MountApp'],
    limitations: 'Asset inventory only. It carries no signal state, timing plan, or preemption status, and Nexus cannot control a signal.',
  },
  {
    code: 'parking-spaces',
    name: 'Downtown parking spaces',
    description: 'Inventoried on-street and kiosk-managed public parking spaces.',
    geometryType: 'polygon',
    layerId: 20,
    fields: ['OBJECTID', 'SPACEID', 'SPACENAME', 'SPACELOC', 'SPACETYPE', 'ACCESSTYPE', 'OPERABLE'],
    limitations: 'Space inventory only. It carries no live occupancy; a space shown here may be full, reserved, or inside a closure.',
  },
];

export interface ReferenceLayer {
  definition: ReferenceLayerDefinition;
  featureCollection: { type: 'FeatureCollection'; features: unknown[] };
  retrievedAt: string;
  attribution: string;
}

interface CachedLayer { layer: ReferenceLayer; expiresAt: number }

const cache = new Map<string, CachedLayer>();

export function referenceLayerByCode(code: string): ReferenceLayerDefinition | undefined {
  return cityReferenceLayers.find(layer => layer.code === code);
}

/** Keeps the payload to the attributes an operator is shown and drops the empty asset columns. */
function trim(feature: unknown, fields: string[]): unknown {
  if (!feature || typeof feature !== 'object') return feature;
  const value = feature as { properties?: Record<string, unknown> };
  if (!value.properties) return feature;
  const properties: Record<string, unknown> = {};
  for (const field of fields) {
    if (value.properties[field] !== undefined && value.properties[field] !== null) {
      properties[field] = value.properties[field];
    }
  }
  return { ...value, properties };
}

export async function loadCityReferenceLayer(
  definition: ReferenceLayerDefinition,
  signal?: AbortSignal,
  now = Date.now(),
): Promise<ReferenceLayer> {
  const cached = cache.get(definition.code);
  if (cached && cached.expiresAt > now) return cached.layer;

  const params = new URLSearchParams({
    where: '1=1',
    geometry: `${EXTENT.west},${EXTENT.south},${EXTENT.east},${EXTENT.north}`,
    geometryType: 'esriGeometryEnvelope',
    inSR: '4326',
    spatialRel: 'esriSpatialRelIntersects',
    outFields: definition.fields.join(','),
    returnGeometry: 'true',
    outSR: '4326',
    f: 'geojson',
  });

  const collection = await fetchJson<{ features?: unknown[]; error?: { message?: string } }>(
    `${MAP_SERVER}/${definition.layerId}/query?${params}`,
    { signal, timeoutMs: 25_000 },
  );
  if (collection.error) {
    throw new ConnectorError('upstream_unavailable', collection.error.message ?? 'City GIS rejected the reference query');
  }

  const layer: ReferenceLayer = {
    definition,
    featureCollection: {
      type: 'FeatureCollection',
      features: (collection.features ?? []).map(feature => trim(feature, definition.fields)),
    },
    retrievedAt: new Date(now).toISOString(),
    attribution: 'City of Auburn GIS',
  };
  cache.set(definition.code, { layer, expiresAt: now + CACHE_TTL_MS });
  return layer;
}

export function clearReferenceLayerCache(): void {
  cache.clear();
}
