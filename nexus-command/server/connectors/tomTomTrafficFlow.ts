import type { NormalizedObservation } from '../operational/domain.js';
import { ConnectorError, type AuthoritativeConnector, type ConnectorBatch, type ConnectorContext, type ConnectorDefinition } from './types.js';
import { fetchJson } from './http.js';
import { canonicalHash } from './normalization.js';

const POINTS = [
  { code: 'i85-exit51', name: 'I-85 Exit 51', lat: 32.5558, lon: -85.5209 },
  { code: 'south-college', name: 'South College Street', lat: 32.5904, lon: -85.4962 },
  { code: 'north-donahue', name: 'North Donahue Drive', lat: 32.6177, lon: -85.4905 },
  { code: 'downtown', name: 'Downtown Auburn', lat: 32.6062, lon: -85.4808 },
];

interface TomTomResponse {
  flowSegmentData?: {
    frc?: string;
    currentSpeed?: number;
    freeFlowSpeed?: number;
    currentTravelTime?: number;
    freeFlowTravelTime?: number;
    confidence?: number;
    roadClosure?: boolean;
    coordinates?: { coordinate?: Array<{ latitude: number; longitude: number }> };
  };
}

export class TomTomTrafficFlowConnector implements AuthoritativeConnector {
  readonly definition: ConnectorDefinition = {
    code: 'tomtom-traffic-flow-v1',
    sourceCode: 'tomtom-traffic-flow',
    name: 'TomTom Traffic Flow',
    ownerAgencyCode: 'traffic-operations',
    ownerAgencyName: 'Traffic Operations',
    sourceType: 'api',
    authority: 'TomTom Traffic API',
    authorityUri: 'https://developer.tomtom.com/traffic-api/documentation/traffic-flow/flow-segment-data',
    schemaVersion: 'tomtom-flow-segment-v4',
    expectedCadenceSeconds: 60,
    staleAfterSeconds: 180,
    dataClassification: 'live',
    permittedUse: 'Licensed real-time speed and travel-time context under the configured TomTom account.',
    partnerApprovalRequired: false,
    defaultConnectionStatus: 'configuration_required',
    requiredEnvironment: ['TOMTOM_API_KEY'],
  };

  isConfigured(): boolean { return Boolean(process.env.TOMTOM_API_KEY); }

  async fetch(context: ConnectorContext): Promise<ConnectorBatch> {
    const key = process.env.TOMTOM_API_KEY;
    if (!key) throw new ConnectorError('configuration_required', 'TOMTOM_API_KEY is not configured');
    const observedAt = new Date().toISOString();
    const observations = await Promise.all(POINTS.map(async point => {
      const url = `https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?point=${point.lat},${point.lon}&unit=MPH&key=${encodeURIComponent(key)}`;
      const payload = await fetchJson<TomTomResponse>(url, { signal: context.signal });
      const flow = payload.flowSegmentData;
      if (!flow) throw new ConnectorError('invalid_payload', `No flow segment for ${point.name}`);
      const normalized = {
        pointCode: point.code,
        pointName: point.name,
        currentSpeedMph: flow.currentSpeed ?? null,
        freeFlowSpeedMph: flow.freeFlowSpeed ?? null,
        currentTravelTimeSeconds: flow.currentTravelTime ?? null,
        freeFlowTravelTimeSeconds: flow.freeFlowTravelTime ?? null,
        confidence: flow.confidence ?? null,
        roadClosure: flow.roadClosure ?? false,
        functionalRoadClass: flow.frc ?? null,
      };
      const ratio = flow.currentSpeed && flow.freeFlowSpeed ? flow.currentSpeed / flow.freeFlowSpeed : null;
      return {
        sourceEventId: `flow:${point.code}:${observedAt.slice(0, 16)}`,
        observedAt,
        summary: `${point.name}: ${flow.currentSpeed ?? 'unknown'} mph${ratio !== null ? ` (${Math.round(ratio * 100)}% of free flow)` : ''}`,
        geometryGeojson: { type: 'Point', coordinates: [point.lon, point.lat] },
        attributes: normalized,
        qualityFlags: flow.confidence !== undefined && flow.confidence < 0.7 ? ['low_confidence'] : [],
        contentHash: canonicalHash(normalized),
        provenance: {
          authority: this.definition.authority,
          authorityUri: this.definition.authorityUri,
          connectorCode: this.definition.code,
          schemaVersion: this.definition.schemaVersion,
          fetchedAt: observedAt,
          upstreamObservedAt: observedAt,
          termsNote: this.definition.permittedUse,
        },
      } satisfies NormalizedObservation;
    }));
    return { observations, upstreamObservedAt: observedAt, checkpoint: { observedAt }, metadata: { pointCount: POINTS.length } };
  }
}
