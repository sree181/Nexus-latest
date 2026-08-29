import { describe, expect, it } from 'vitest';
import { resolveServiceRole } from './serviceRole.js';

describe('service role', () => {
  it('defaults to the API when nothing is configured', () => {
    expect(resolveServiceRole(undefined)).toBe('api');
    expect(resolveServiceRole('')).toBe('api');
    expect(resolveServiceRole('  ')).toBe('api');
  });

  it('selects the connector worker when the deployment asks for it', () => {
    expect(resolveServiceRole('connector-worker')).toBe('connector-worker');
    expect(resolveServiceRole(' connector-worker ')).toBe('connector-worker');
  });

  it('refuses to boot on a typo instead of silently starting a second API', () => {
    expect(() => resolveServiceRole('worker')).toThrow(/connector-worker/);
    expect(() => resolveServiceRole('Connector-Worker')).toThrow(/received "Connector-Worker"/);
  });
});
