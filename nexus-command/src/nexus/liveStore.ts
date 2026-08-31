import { graphApi } from '../graphApi';
import { OperationalApiError, operationalApi } from '../operationalApi';
import type { GraphSnapshot } from '../graphTypes';
import type {
  OperationalSnapshot,
  PrincipalContext,
  ScenarioPack,
  SystemStatus,
} from '../operationalTypes';

export interface LiveBundle {
  loading: boolean;
  error: string | null;
  noWindow: boolean;
  status: SystemStatus | null;
  principal: PrincipalContext | null;
  snapshot: OperationalSnapshot | null;
  graph: GraphSnapshot | null;
  packs: ScenarioPack[];
  fetchedAt: number | null;
}

const EMPTY: LiveBundle = {
  loading: true,
  error: null,
  noWindow: false,
  status: null,
  principal: null,
  snapshot: null,
  graph: null,
  packs: [],
  fetchedAt: null,
};

let bundle: LiveBundle = EMPTY;
const listeners = new Set<() => void>();
let timer: number | null = null;
let subscribers = 0;
let inFlight = false;

function emit() {
  for (const listener of listeners) listener();
}

async function load() {
  if (inFlight) return;
  inFlight = true;
  try {
    const [status, principal] = await Promise.all([
      operationalApi.status(),
      operationalApi.principal().catch(() => null),
    ]);
    operationalApi.scenarioPacks().then(packs => {
      bundle = { ...bundle, packs };
      emit();
    }).catch(() => undefined);

    let event;
    try {
      event = await operationalApi.activeEvent();
    } catch (reason) {
      if (reason instanceof OperationalApiError && reason.code === 'NO_ACTIVE_EVENT') {
        bundle = {
          ...bundle,
          loading: false,
          error: null,
          noWindow: true,
          status,
          principal,
          snapshot: null,
          graph: null,
          fetchedAt: Date.now(),
        };
        emit();
        return;
      }
      throw reason;
    }

    const [snapshot, graph] = await Promise.all([
      operationalApi.snapshot(event.eventId),
      graphApi.snapshot(event.eventId, 'decision_lineage').catch(() => null),
    ]);

    bundle = {
      ...bundle,
      loading: false,
      error: null,
      noWindow: false,
      status,
      principal,
      snapshot,
      graph,
      fetchedAt: Date.now(),
    };
    emit();
  } catch (reason) {
    if (reason instanceof OperationalApiError && (reason.status === 401 || reason.code === 'IDENTITY_CLAIMS_INCOMPLETE')) {
      window.location.assign('/');
      return;
    }
    bundle = {
      ...bundle,
      loading: false,
      error: reason instanceof Error ? reason.message : 'Nexus could not be reached.',
    };
    emit();
  } finally {
    inFlight = false;
  }
}

export function getLive(): LiveBundle {
  return bundle;
}

export function subscribeLive(listener: () => void): () => void {
  listeners.add(listener);
  subscribers += 1;
  if (subscribers === 1) {
    void load();
    timer = window.setInterval(() => void load(), 10_000);
  }
  return () => {
    listeners.delete(listener);
    subscribers = Math.max(0, subscribers - 1);
    if (subscribers === 0 && timer) {
      window.clearInterval(timer);
      timer = null;
    }
  };
}

export function reloadLive(): Promise<void> {
  return load();
}
