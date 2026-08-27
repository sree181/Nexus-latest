import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchJson } from './http.js';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchJson', () => {
  it('retries a transient network failure and returns the authoritative JSON response', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error('temporary network failure'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(fetchJson<{ status: string }>('https://authority.example/data', { attempts: 2, retryDelayMs: 1 }))
      .resolves.toEqual({ status: 'ok' });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not retry an upstream permission denial', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 403 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(fetchJson('https://authority.example/restricted', { attempts: 3, retryDelayMs: 1 }))
      .rejects.toMatchObject({ category: 'permission_required' });
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
