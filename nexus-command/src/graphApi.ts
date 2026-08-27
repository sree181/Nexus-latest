import type { OperationalEvent } from './operationalTypes';
import type { AgencyCoordination, GraphSnapshot, GraphView } from './graphTypes';

interface Envelope<T> { data: T; requestId: string }

async function graphRequest<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { cache: 'no-store', credentials: 'include' });
  const payload = await response.json() as Envelope<T> | { error: { code: string; message: string } };
  if (!response.ok || 'error' in payload) {
    const error = 'error' in payload ? payload.error : { code: 'GRAPH_REQUEST_FAILED', message: 'Graph request failed' };
    throw new Error(`${error.code}: ${error.message}`);
  }
  return payload.data;
}

export const graphApi = {
  activeEvent(): Promise<OperationalEvent> {
    return graphRequest('/events/active?mode=live');
  },
  snapshot(eventId: string, view: GraphView): Promise<GraphSnapshot> {
    return graphRequest(`/events/${eventId}/graph?mode=live&view=${view}`);
  },
  neighborhood(eventId: string, nodeId: string, depth = 1): Promise<GraphSnapshot> {
    return graphRequest(`/events/${eventId}/graph/nodes/${nodeId}/neighborhood?mode=live&depth=${depth}`);
  },
  coordination(eventId: string): Promise<AgencyCoordination> {
    return graphRequest(`/events/${eventId}/graph/agency-coordination?mode=live`);
  },
};
