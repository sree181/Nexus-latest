import { AldotTrafficCountsConnector } from './aldotTrafficCounts.js';
import { AlgoTrafficConnector } from './algoTraffic.js';
import { CityRoadClosuresConnector } from './cityRoadClosures.js';
import { EtaSpotTransitConnector } from './etaSpotTransit.js';
import { NwsWeatherConnector } from './nwsWeather.js';
import { parkingOccupancyConnector, emergencyAccessConnector } from './partnerGated.js';
import { TomTomTrafficFlowConnector } from './tomTomTrafficFlow.js';
import type { AuthoritativeConnector } from './types.js';
import { UsgsHazardsConnector } from './usgsHazards.js';

export const authoritativeConnectors: AuthoritativeConnector[] = [
  new CityRoadClosuresConnector(),
  new AlgoTrafficConnector(),
  new NwsWeatherConnector(),
  new UsgsHazardsConnector(),
  new EtaSpotTransitConnector(),
  new TomTomTrafficFlowConnector(),
  new AldotTrafficCountsConnector(),
  parkingOccupancyConnector,
  emergencyAccessConnector,
];

export function connectorByCode(code: string): AuthoritativeConnector | undefined {
  return authoritativeConnectors.find(connector => connector.definition.code === code);
}
