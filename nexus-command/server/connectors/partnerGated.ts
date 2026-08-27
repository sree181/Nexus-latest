import { ConnectorError, type AuthoritativeConnector, type ConnectorBatch, type ConnectorContext, type ConnectorDefinition } from './types.js';

export class PartnerGatedConnector implements AuthoritativeConnector {
  constructor(readonly definition: ConnectorDefinition) {}
  isConfigured(): boolean { return false; }
  async fetch(_context: ConnectorContext): Promise<ConnectorBatch> {
    throw new ConnectorError('permission_required', `${this.definition.name} requires an approved agency data-sharing agreement and connector credentials`);
  }
}

export const parkingOccupancyConnector = new PartnerGatedConnector({
  code: 'auburn-parking-occupancy-v1', sourceCode: 'auburn-parking-occupancy', name: 'Auburn Parking Occupancy',
  ownerAgencyCode: 'auburn-parking', ownerAgencyName: 'Auburn University Parking Services', sourceType: 'api', authority: 'Auburn University Parking Services / FoPark',
  authorityUri: 'https://au-parking.com/', schemaVersion: 'partner-contract-required', expectedCadenceSeconds: 30,
  staleAfterSeconds: 120, dataClassification: 'restricted',
  permittedUse: 'Lot-level capacity and occupancy only under an approved Auburn/FoPark interface; no camera imagery or individual vehicle records.',
  partnerApprovalRequired: true, defaultConnectionStatus: 'permission_required', requiredEnvironment: ['AUBURN_PARKING_API_URL', 'AUBURN_PARKING_API_TOKEN'],
});

export const emergencyAccessConnector = new PartnerGatedConnector({
  code: 'auburn-emergency-access-v1', sourceCode: 'auburn-emergency-access', name: 'Game Day Emergency Access',
  ownerAgencyCode: 'public-safety', ownerAgencyName: 'Auburn Public Safety / Game Day Event Command', sourceType: 'webhook', authority: 'Auburn Public Safety / Game Day Event Command',
  authorityUri: 'https://www.auburnal.gov/public-safety/', schemaVersion: 'agency-contract-required', expectedCadenceSeconds: null,
  staleAfterSeconds: 120, dataClassification: 'restricted',
  permittedUse: 'Corridor operational state and restrictions supplied by authorized Event Command; no dispatch, patient, or law-enforcement-sensitive data.',
  partnerApprovalRequired: true, defaultConnectionStatus: 'permission_required', requiredEnvironment: ['EMERGENCY_ACCESS_WEBHOOK_SECRET'],
});
