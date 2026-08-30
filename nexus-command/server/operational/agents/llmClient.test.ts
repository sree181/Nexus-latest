import { describe, expect, it } from 'vitest';
import { retryDelayMs } from './llmClient.js';

describe('desk LLM retry', () => {
  it('retries 429 using the provider wait when present', () => {
    expect(retryDelayMs(429, 'Rate limit reached. Please try again in 1250ms', 0)).toBe(1250);
    expect(retryDelayMs(429, 'Please try again in 14.4375s. Need more tokens?', 0)).toBe(8000);
  });

  it('retries 503 with a short backoff', () => {
    expect(retryDelayMs(503, 'unavailable', 0)).toBe(300);
    expect(retryDelayMs(503, 'unavailable', 2)).toBe(900);
  });

  it('does not retry ordinary client errors', () => {
    expect(retryDelayMs(400, 'bad request', 0)).toBeNull();
    expect(retryDelayMs(401, 'unauthorized', 0)).toBeNull();
  });
});
