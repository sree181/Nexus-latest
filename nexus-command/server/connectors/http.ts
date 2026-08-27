import { ConnectorError } from './types.js';

export interface FetchJsonOptions {
  timeoutMs?: number;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  attempts?: number;
  retryDelayMs?: number;
}

function retryable(error: ConnectorError): boolean {
  return ['network_error', 'upstream_timeout', 'upstream_unavailable', 'rate_limited'].includes(error.category);
}

async function wait(delayMs: number, signal?: AbortSignal): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, delayMs);
    const abort = () => { clearTimeout(timer); reject(new ConnectorError('upstream_timeout', 'Connector run was cancelled')); };
    if (signal?.aborted) abort();
    else signal?.addEventListener('abort', abort, { once: true });
  });
}

export async function fetchJson<T>(url: string, options: FetchJsonOptions = {}): Promise<T> {
  const attempts = Math.max(1, options.attempts ?? 3);
  let lastError: ConnectorError | null = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const timeoutController = new AbortController();
    const timeout = setTimeout(() => timeoutController.abort(), options.timeoutMs ?? 15_000);
    const abort = () => timeoutController.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    try {
      const response = await fetch(url, {
        signal: timeoutController.signal,
        headers: {
          Accept: 'application/json',
          'User-Agent': 'Nexus-Coordinate/1.0 (+authoritative-operational-ingestion)',
          ...options.headers,
        },
      });
      if (response.status === 401 || response.status === 403) {
        throw new ConnectorError('permission_required', `Upstream denied access (${response.status})`, response.status);
      }
      if (response.status === 429) {
        const retryAfter = Number(response.headers.get('retry-after'));
        throw new ConnectorError('rate_limited', 'Upstream rate limit reached', response.status, Number.isFinite(retryAfter) ? retryAfter : undefined);
      }
      if (!response.ok) {
        throw new ConnectorError('upstream_unavailable', `Upstream returned ${response.status}`, response.status);
      }
      try {
        return await response.json() as T;
      } catch {
        throw new ConnectorError('invalid_payload', 'Upstream returned invalid JSON', response.status);
      }
    } catch (error) {
      lastError = error instanceof ConnectorError
        ? error
        : timeoutController.signal.aborted
          ? new ConnectorError('upstream_timeout', 'Upstream request timed out')
          : new ConnectorError('network_error', error instanceof Error ? error.message : 'Network request failed');
    } finally {
      clearTimeout(timeout);
      options.signal?.removeEventListener('abort', abort);
    }

    if (!lastError || !retryable(lastError) || attempt === attempts || options.signal?.aborted) throw lastError;
    const retryAfterMs = lastError.retryAfterSeconds ? lastError.retryAfterSeconds * 1_000 : 0;
    await wait(Math.max(retryAfterMs, (options.retryDelayMs ?? 500) * attempt), options.signal);
  }
  throw lastError ?? new ConnectorError('network_error', 'Authoritative request failed');
}
