import { clearSession, getAccessToken } from './auth/session';
import type {
  ApiEnvelope,
  ApiError,
  Commitment,
  OperationalEvent,
  OperationalSnapshot,
  PrincipalContext,
  Recommendation,
  RecommendationState,
  ReferenceLayer,
  ReferenceLayerDefinition,
  ScenarioPack,
  SystemStatus,
} from './operationalTypes';

export class OperationalApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'OperationalApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getAccessToken();
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    cache: 'no-store',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
  });
  const payload = await response.json() as ApiEnvelope<T> | ApiError;
  if (!response.ok || 'error' in payload) {
    const error = 'error' in payload ? payload.error : { code: 'REQUEST_FAILED', message: 'Request failed' };
    if (response.status === 401 || error.code === 'IDENTITY_CLAIMS_INCOMPLETE') {
      clearSession();
    }
    throw new OperationalApiError(response.status, error.code, error.message, error.details);
  }
  return payload.data;
}

export const operationalApi = {
  principal(): Promise<PrincipalContext> {
    return request('/me');
  },
  status(): Promise<SystemStatus> {
    return request('/system/status');
  },
  activeEvent(): Promise<OperationalEvent> {
    return request('/events/active?mode=live');
  },
  scenarioPacks(): Promise<ScenarioPack[]> {
    return request('/scenario-packs');
  },
  referenceLayers(): Promise<ReferenceLayerDefinition[]> {
    return request('/reference-layers');
  },
  referenceLayer(code: string): Promise<ReferenceLayer> {
    return request(`/reference-layers/${code}`);
  },
  openOperatingWindow(input: {
    packCode: string;
    name: string;
    locationName: string;
    endsAt?: string | null;
  }): Promise<OperationalEvent> {
    return request('/events', {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ ...input, mode: 'live' }),
    });
  },
  closeOperatingWindow(eventId: string): Promise<OperationalEvent> {
    return request(`/events/${eventId}/close`, { method: 'POST' });
  },
  snapshot(eventId: string): Promise<OperationalSnapshot> {
    return request(`/events/${eventId}/snapshot`);
  },
  recommendation(recommendationId: string): Promise<Recommendation> {
    return request(`/recommendations/${recommendationId}`);
  },
  decide(
    recommendation: Recommendation,
    action: 'approve' | 'reject' | 'request_revision' | 'delegate' | 'escalate',
    reasonCode: string,
    comment?: string,
  ): Promise<{ recommendation: Recommendation; createdCommitments: Commitment[] }> {
    return request(`/recommendations/${recommendation.recommendationId}/decisions`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({
        action,
        recommendationVersion: recommendation.version,
        expectedState: recommendation.state as RecommendationState,
        evidenceSnapshotHash: recommendation.evidenceSnapshotHash,
        reasonCode,
        comment,
        confirmationTextHash: action === 'approve' ? recommendation.evidenceSnapshotHash : undefined,
      }),
    });
  },
  transitionCommitment(
    commitment: Commitment,
    targetState: Commitment['state'],
    reasonCode: string,
    comment?: string,
    evidenceIds?: string[],
  ): Promise<Commitment> {
    return request(`/commitments/${commitment.commitmentId}/transitions`, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({
        expectedVersion: commitment.version,
        targetState,
        reasonCode,
        comment,
        evidenceIds,
      }),
    });
  },
};
