import { fetchJson } from '../connectors/http.js';
import {
  AUBURN_WEATHER_ZONES,
  summarizeForecast,
  weatherOverlayFeatures,
} from '../connectors/nwsWeather.js';

const AUTHORITY_URI = 'https://api.weather.gov';
const FORECAST_GRID = { office: 'BMX', x: 111, y: 47 };

export interface WeatherOverlay {
  retrievedAt: string;
  attribution: string;
  limitations: string;
  alertCount: number;
  forecastSummary: string | null;
  featureCollection: { type: 'FeatureCollection'; features: unknown[] };
}

interface AlertFeature {
  geometry?: Record<string, unknown> | null;
  properties?: Record<string, unknown> & {
    id?: string;
    event?: string;
    headline?: string | null;
    status?: string;
    messageType?: string;
    expires?: string | null;
    areaDesc?: string;
    geocode?: { UGC?: string[] };
    parameters?: Record<string, unknown>;
  };
}

interface ForecastPeriod {
  startTime?: string;
  temperature?: number;
  temperatureUnit?: string;
  probabilityOfPrecipitation?: { value?: number | null } | null;
  shortForecast?: string;
}

/**
 * Live NWS products for the map only. This does not write evidence and cannot open an incident.
 */
export async function loadWeatherOverlay(signal?: AbortSignal): Promise<WeatherOverlay> {
  const retrievedAt = new Date().toISOString();
  const [alerts, forecast] = await Promise.all([
    fetchJson<{ features?: AlertFeature[] }>(`${AUTHORITY_URI}/alerts/active?area=AL`, { signal }),
    fetchJson<{ properties?: { periods?: ForecastPeriod[] } }>(
      `${AUTHORITY_URI}/gridpoints/${FORECAST_GRID.office}/${FORECAST_GRID.x},${FORECAST_GRID.y}/forecast/hourly`,
      { signal },
    ),
  ]);

  const features = weatherOverlayFeatures(alerts.features ?? [], Date.parse(retrievedAt));
  const summary = summarizeForecast(forecast.properties?.periods ?? []);
  const first = summary.hours[0];
  const forecastSummary = first
    ? `Next ${summary.hours.length}h at Jordan-Hare: ${String(first.shortForecast ?? 'no forecast text')}, up to ${
      summary.maxTemperatureF ?? 'unknown'
    }F, ${summary.maxPrecipitationProbability ?? 0}% precipitation`
    : null;

  return {
    retrievedAt,
    attribution: 'NOAA National Weather Service',
    limitations: 'Official NWS watches, warnings, and advisories for Lee County, plus the hourly grid at Jordan-Hare. Nexus reproduces the product. It does not issue, amend, or cancel an alert, and this overlay does not open an incident.',
    alertCount: features.length,
    forecastSummary,
    featureCollection: { type: 'FeatureCollection', features },
  };
}

export const weatherOverlayMeta = {
  zones: AUBURN_WEATHER_ZONES,
  grid: `${FORECAST_GRID.office}/${FORECAST_GRID.x},${FORECAST_GRID.y}`,
};
