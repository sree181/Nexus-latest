import type { NormalizedObservation } from '../operational/domain.js';
import { fetchJson } from './http.js';
import { asIsoDate, canonicalHash, qualityFlagsForAge } from './normalization.js';
import type { AuthoritativeConnector, ConnectorBatch, ConnectorContext, ConnectorDefinition } from './types.js';

const AUTHORITY_URI = 'https://auburn.etaspot.net/';
const TOKEN = process.env.ETA_SPOT_PUBLIC_TOKEN ?? 'TESTING';

interface EtaVehicle {
  routeID: number;
  patternID: number;
  equipmentID: string;
  tripID?: string | null;
  lat: number;
  lng: number;
  load?: number;
  capacity?: number;
  nextStopID?: number;
  nextStopETA?: number;
  inService?: number;
  onSchedule?: number | null;
  receiveTime: number;
  vehicleType?: string;
}
interface EtaRoute { id: number; name: string; abbr?: string; type?: string; }
interface EtaVehiclesResponse { get_vehicles?: EtaVehicle[]; }
interface EtaRoutesResponse { get_routes?: EtaRoute[]; }

export class EtaSpotTransitConnector implements AuthoritativeConnector {
  readonly definition: ConnectorDefinition = {
    code: 'auburn-eta-spot-v1',
    sourceCode: 'tiger-transit-eta-spot',
    name: 'Tiger Transit ETA Spot',
    ownerAgencyCode: 'auburn-transit',
    ownerAgencyName: 'Auburn University Transportation Services',
    sourceType: 'api',
    authority: 'Auburn University Transportation Services / ETA Transit Systems',
    authorityUri: AUTHORITY_URI,
    schemaVersion: 'eta-spot-vehicles-v1',
    expectedCadenceSeconds: 15,
    staleAfterSeconds: 90,
    dataClassification: 'live',
    permittedUse: 'Read-only public vehicle location and service status; production cadence requires owner confirmation.',
    partnerApprovalRequired: true,
    defaultConnectionStatus: 'permission_required',
    requiredEnvironment: [],
  };

  isConfigured(): boolean {
    return process.env.ETA_SPOT_PRODUCTION_APPROVED === 'true' || process.env.NEXUS_ENABLE_PUBLIC_FEEDS === 'true';
  }

  async fetch(context: ConnectorContext): Promise<ConnectorBatch> {
    const fetchedAt = new Date().toISOString();
    const [vehiclePayload, routePayload] = await Promise.all([
      fetchJson<EtaVehiclesResponse>(`${AUTHORITY_URI}service.php?service=get_vehicles&token=${encodeURIComponent(TOKEN)}`, { signal: context.signal }),
      fetchJson<EtaRoutesResponse>(`${AUTHORITY_URI}service.php?service=get_routes&token=${encodeURIComponent(TOKEN)}`, { signal: context.signal }),
    ]);
    const routeNames = new Map((routePayload.get_routes ?? []).map(route => [route.id, route.name]));
    const observations: NormalizedObservation[] = (vehiclePayload.get_vehicles ?? [])
      .filter(vehicle => Number.isFinite(vehicle.lat) && Number.isFinite(vehicle.lng))
      .filter(vehicle => {
        const ageSeconds = Math.max(0, (Date.now() - new Date(asIsoDate(vehicle.receiveTime)).getTime()) / 1000);
        return vehicle.inService === 1 || ageSeconds <= 600;
      })
      .map(vehicle => {
        const observedAt = asIsoDate(vehicle.receiveTime);
        const routeName = routeNames.get(vehicle.routeID) ?? `Route ${vehicle.routeID}`;
        const normalized = {
          equipmentId: vehicle.equipmentID,
          routeId: vehicle.routeID,
          routeName,
          patternId: vehicle.patternID,
          tripId: vehicle.tripID ?? null,
          latitude: vehicle.lat,
          longitude: vehicle.lng,
          inService: vehicle.inService === 1,
          load: vehicle.load ?? null,
          capacity: vehicle.capacity ?? null,
          nextStopId: vehicle.nextStopID ?? null,
          nextStopEtaSeconds: vehicle.nextStopETA ?? null,
          scheduleDeviationSeconds: vehicle.onSchedule ?? null,
          vehicleType: vehicle.vehicleType ?? 'Bus',
        };
        return {
          sourceEventId: `vehicle:${vehicle.equipmentID}:${vehicle.receiveTime}`,
          observedAt,
          summary: `${routeName}: vehicle ${vehicle.equipmentID} ${vehicle.inService === 1 ? 'in service' : 'not in service'}`,
          geometryGeojson: { type: 'Point', coordinates: [vehicle.lng, vehicle.lat] },
          attributes: normalized,
          qualityFlags: qualityFlagsForAge(observedAt, this.definition.staleAfterSeconds),
          contentHash: canonicalHash(normalized),
          provenance: {
            authority: this.definition.authority,
            authorityUri: AUTHORITY_URI,
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
      checkpoint: { fetchedAt, vehicleCount: observations.length },
      metadata: { routeCount: routeNames.size, publicTokenSource: process.env.ETA_SPOT_PUBLIC_TOKEN ? 'environment' : 'official_web_client' },
    };
  }
}
